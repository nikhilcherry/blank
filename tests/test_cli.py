import datetime as dt
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from blank import term
from blank.cli import main

ENV = {
    "GIT_AUTHOR_NAME": "Ada", "GIT_AUTHOR_EMAIL": "ada@example.com",
    "GIT_COMMITTER_NAME": "Ada", "GIT_COMMITTER_EMAIL": "ada@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}


def build_repo(root: Path) -> None:
    def git(*args):
        subprocess.run(["git", "-C", str(root), *args], check=True,
                       capture_output=True, env={**os.environ, **ENV})

    git("init", "-q", "-b", "main")
    (root / "src").mkdir()
    for step in range(6):
        (root / "src" / "core.py").write_text(
            "def handle(x):\n" + "".join(f"{'    ' * (i + 1)}if x > {i}:\n" for i in range(step + 1))
            + f"{'    ' * (step + 2)}return x\n"
        )
        (root / "src" / "util.py").write_text(f"def helper():\n    return {step}\n")
        (root / "README.md").write_text(f"# demo\n\nrevision {step}\n")
        git("add", "-A")
        git("commit", "-qm", f"step {step}")


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        build_repo(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_stats(self):
        code, out, _ = self.run_cli("stats", str(self.root), "--no-color")
        self.assertEqual(code, 0)
        self.assertIn("6 commits", out)
        self.assertIn("bus factor", out)
        self.assertIn("core.py", out)

    def test_scan_writes_self_contained_html(self):
        target = self.root / "out" / "report.html"
        code, out, _ = self.run_cli("scan", str(self.root), "-o", str(target), "--no-color")
        self.assertEqual(code, 0)
        html = target.read_text()
        self.assertTrue(html.startswith("<!doctype html>"))
        self.assertIn("</html>", html)
        self.assertIn("core.py", html)
        self.assertNotIn("<link", html)

    def test_scan_json_to_stdout_is_pipeable(self):
        # `blank scan . -o /dev/null --json - | jq` must work, so stdout has to
        # be nothing but the JSON — progress lines belong on stderr.
        code, out, err = self.run_cli("scan", str(self.root), "-o", str(self.root / "r.html"),
                                      "--json", "-", "--no-color")
        self.assertEqual(code, 0)
        self.assertTrue(out.lstrip().startswith("{"), f"stdout polluted: {out[:80]!r}")
        payload = json.loads(out)
        self.assertEqual(payload["window"]["commits"], 6)
        self.assertIn("report", err)

    def test_markdown_to_stdout_is_pipeable(self):
        code, out, err = self.run_cli("scan", str(self.root), "-o", str(self.root / "r.html"),
                                      "--markdown", "-", "--no-color")
        self.assertEqual(code, 0)
        self.assertTrue(out.lstrip().startswith("##"), f"stdout polluted: {out[:80]!r}")
        self.assertIn("report", err)

    def test_hotspots_and_authors_and_coupling(self):
        for command in ("hotspots", "authors", "coupling"):
            code, out, _ = self.run_cli(command, str(self.root), "--no-color")
            self.assertEqual(code, 0, command)
            self.assertTrue(out.strip(), command)

    def test_check_passes_with_loose_thresholds(self):
        code, out, _ = self.run_cli("check", str(self.root), "--min-bus-factor", "1", "--no-color")
        self.assertEqual(code, 0)
        self.assertIn("passed", out)

    def test_check_fails_on_bus_factor(self):
        code, out, _ = self.run_cli("check", str(self.root), "--min-bus-factor", "3", "--no-color")
        self.assertEqual(code, 1)
        self.assertIn("bus factor", out)

    def test_check_without_thresholds_is_an_error(self):
        code, _, err = self.run_cli("check", str(self.root), "--no-color")
        self.assertEqual(code, 2)
        self.assertIn("no thresholds", err)

    def test_since_window_can_exclude_everything(self):
        # git's approxidate silently ignores years it cannot parse (2999 comes
        # back as "no filter"), so pick a future date it definitely accepts.
        future = dt.date.today().replace(year=dt.date.today().year + 5).isoformat()
        code, out, _ = self.run_cli("stats", str(self.root), "--since", future, "--no-color")
        self.assertEqual(code, 0)
        self.assertIn("no commits", out)

    def test_thin_history_is_called_out_rather_than_faked(self):
        # A single-commit repo has real files but no revision history at all.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "-C", str(root), "init", "-q", "-b", "main"],
                           check=True, capture_output=True, env={**os.environ, **ENV})
            (root / "app.py").write_text("def f():\n" + "    " * 3 + "return 1\n")
            for cmd in (["add", "-A"], ["commit", "-qm", "initial import"]):
                subprocess.run(["git", "-C", str(root), *cmd], check=True,
                               capture_output=True, env={**os.environ, **ENV})

            code, out, _ = self.run_cli("stats", str(root), "--no-color")
            self.assertEqual(code, 0)
            self.assertIn("thin history", out)
            self.assertIn("nothing to rank", out)

            html = root / "r.html"
            self.run_cli("scan", str(root), "-o", str(html), "--no-color")
            self.assertIn("Thin history", html.read_text())

    def test_diff_of_a_report_against_itself_is_quiet(self):
        target = self.root / "r.json"
        self.run_cli("scan", str(self.root), "-o", str(self.root / "r.html"),
                     "--json", str(target), "--no-color")
        code, out, _ = self.run_cli("diff", str(target), str(target), "--no-color")
        self.assertEqual(code, 0)
        self.assertIn("no file moved", out)

    def _write_report(self, name, hotspots):
        """A minimal report payload. The scoring itself is covered elsewhere;
        what matters here is argument wiring and exit codes."""
        path = self.root / name
        path.write_text(json.dumps({
            "tool": "blank", "version": "0.1.0",
            "repository": {"name": "demo", "branch": "main", "remote": None, "root": "/tmp"},
            "generated": "2024-06-01T12:00:00",
            "window": {"since": None, "commits": 10,
                       "first_commit": "2024-01-01T00:00:00", "last_commit": "2024-06-01T00:00:00"},
            "summary": {"files": 3, "lines": 300, "authors": 2,
                        "bus_factor": 1, "commits_per_week": 2.0},
            "languages": [], "authors": [], "knowledge_risk": [], "coupling": [],
            "hotspots": [{"path": p, "risk": r} for p, r in hotspots],
        }))
        return path

    def test_diff_fails_on_regression_when_asked(self):
        old = self._write_report("old.json", [("src/core.py", 0.20)])
        new = self._write_report("new.json", [("src/core.py", 0.80)])

        code, _, err = self.run_cli("diff", str(old), str(new),
                                    "--fail-on-regression", "--no-color")
        self.assertEqual(code, 1)
        self.assertIn("riskier", err)

        code, _, _ = self.run_cli("diff", str(old), str(new), "--no-color")
        self.assertEqual(code, 0, "diff without a gate flag must not fail the build")

    def test_diff_max_increase_threshold(self):
        old = self._write_report("old.json", [("src/core.py", 0.20)])
        new = self._write_report("new.json", [("src/core.py", 0.45)])

        code, _, _ = self.run_cli("diff", str(old), str(new), "--max-increase", "0.5", "--no-color")
        self.assertEqual(code, 0, "a 0.25 increase is under the 0.5 limit")

        code, _, err = self.run_cli("diff", str(old), str(new), "--max-increase", "0.1", "--no-color")
        self.assertEqual(code, 1)
        self.assertIn("0.25", err)

    def test_diff_writes_markdown(self):
        old = self._write_report("old.json", [("src/core.py", 0.20)])
        new = self._write_report("new.json", [("src/core.py", 0.80)])
        code, out, _ = self.run_cli("diff", str(old), str(new), "--markdown", "-", "--no-color")
        self.assertEqual(code, 0)
        self.assertIn("what moved", out)
        self.assertIn("`src/core.py`", out)

    def test_diff_rejects_a_file_that_is_not_a_report(self):
        junk = self.root / "junk.json"
        junk.write_text('{"tool": "not-blank"}')
        code, _, err = self.run_cli("diff", str(junk), str(junk), "--no-color")
        self.assertEqual(code, 2)
        self.assertIn("not a blank report", err)

    def test_non_repository_path_exits_two(self):
        with TemporaryDirectory() as plain:
            code, _, err = self.run_cli("stats", plain, "--no-color")
            self.assertEqual(code, 2)
            self.assertIn("error", err)

    def test_no_command_prints_help(self):
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("usage", out)

    def test_module_entry_point(self):
        proc = subprocess.run(
            [sys.executable, "-m", "blank", "--version"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("blank", proc.stdout)


class TermTests(unittest.TestCase):
    def test_sparkline_scales(self):
        self.assertEqual(term.sparkline([]), "")
        self.assertEqual(term.sparkline([0, 0]), "▁▁")
        line = term.sparkline([1, 5, 10])
        self.assertEqual(len(line), 3)
        self.assertEqual(line[-1], "█")

    def test_bar_fills_proportionally(self):
        self.assertEqual(term.bar(5, 10, 10).count("█"), 5)
        self.assertEqual(term.bar(0, 0, 4), "░░░░")

    def test_display_width_handles_combining_and_wide_chars(self):
        self.assertEqual(term.display_width("abc"), 3)
        # Decomposed "Neuhäuser": the combining diaeresis takes no column.
        self.assertEqual(term.display_width("Neuha\u0308user"), 9)
        self.assertEqual(term.display_width("\u6f22\u5b57"), 4)  # CJK is double width

    def test_pad_uses_display_width(self):
        self.assertEqual(len(term.pad("abc", 6)), 6)
        self.assertEqual(term.display_width(term.pad("Neuha\u0308user", 12)), 12)
        self.assertEqual(term.display_width(term.pad("\u6f22\u5b57", 8)), 8)
        self.assertEqual(term.pad("toolong", 3), "toolong")

    def test_truncate_respects_display_width(self):
        self.assertLessEqual(term.display_width(term.truncate("\u6f22" * 20, 10)), 10)
        self.assertLessEqual(term.display_width(term.truncate("a/b/c/d/e/file.py", 8)), 8)

    def test_truncate_keeps_the_tail(self):
        self.assertEqual(term.truncate("abc", 10), "abc")
        self.assertTrue(term.truncate("a/very/long/path/file.py", 10).endswith("file.py"))
        self.assertEqual(len(term.truncate("a/very/long/path/file.py", 10)), 10)

    def test_style_respects_no_color(self):
        self.assertEqual(term.Style(False).red("x"), "x")
        self.assertIn("\033[", term.Style(True).red("x"))

    def test_make_style_honours_env(self):
        old = os.environ.get("NO_COLOR")
        os.environ["NO_COLOR"] = "1"
        try:
            self.assertFalse(term.make_style().enabled)
        finally:
            if old is None:
                os.environ.pop("NO_COLOR")
            else:
                os.environ["NO_COLOR"] = old


if __name__ == "__main__":
    unittest.main()

import datetime as dt
import io
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

    def test_scan_json_to_stdout(self):
        import json
        code, out, _ = self.run_cli("scan", str(self.root), "-o", str(self.root / "r.html"),
                                    "--json", "-", "--no-color")
        self.assertEqual(code, 0)
        payload = json.loads(out[out.index("{"):])
        self.assertEqual(payload["window"]["commits"], 6)

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

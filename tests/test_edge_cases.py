"""Repositories that are unusual but entirely legal.

Every case here was found by running blank against a repository built to look
like the awkward ones in the wild, not by reading the code.
"""

import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from blank import gitlog
from blank.analyze import build_report
from blank.gitlog import GitError
from blank.render import render_html

ENV = {
    "GIT_AUTHOR_NAME": "Ada", "GIT_AUTHOR_EMAIL": "ada@example.com",
    "GIT_COMMITTER_NAME": "Ada", "GIT_COMMITTER_EMAIL": "ada@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}


def git(root, *args, check=True):
    return subprocess.run(["git", "-C", str(root), *args], check=check,
                          capture_output=True, text=True, env={**os.environ, **ENV})


class UnicodePathTests(unittest.TestCase):
    """git escapes non-ASCII paths unless core.quotepath is off.

    Without the fix these files vanish from the report entirely — the scan
    reports zero files for a repository that plainly has several.
    """

    NAMES = [
        "über.py",                 # Latin-1 supplement
        "日本語.py",                # CJK
        "dir with spaces/ok.py",   # spaces, quoted by git for a different reason
        "emoji 🎉.py",             # outside the BMP
    ]

    @staticmethod
    def _body(depth, revision):
        """A function nested *depth* levels deep, so files differ in complexity.

        Padded past the five-code-line floor below which complexity scores 0 —
        otherwise the smallest fixture is unscoreable and never reaches the page.
        """
        lines = [f"CONSTANT_{n} = {n}" for n in range(6)]
        lines.append("def f(x):")
        lines += [f"{'    ' * (level + 1)}if x > {level}:" for level in range(depth)]
        lines.append(f"{'    ' * (depth + 1)}return {revision}")
        return "\n".join(lines) + "\n"

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, "init", "-q", "-b", "main")
        (self.root / "dir with spaces").mkdir()
        # Both axes must vary. Risk is scored relative to the repository, so
        # files identical in depth *or* in revision count all normalise to
        # zero, and an empty ranking would hide the very bug this class is for.
        for index, name in enumerate(self.NAMES):
            for revision in range(index + 2):
                (self.root / name).write_text(self._body(index + 2, revision))
                git(self.root, "add", "-A")
                git(self.root, "commit", "-qm", f"{name} r{revision}")

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_file_is_seen(self):
        report = build_report(self.root)
        self.assertEqual({f.path for f in report.files}, set(self.NAMES))

    def test_history_attaches_to_the_real_paths(self):
        report = build_report(self.root)
        by_path = {f.path: f for f in report.files}
        for index, name in enumerate(self.NAMES):
            self.assertEqual(by_path[name].commits, index + 2, name)
            self.assertGreater(by_path[name].churn, 0, name)

    def test_names_survive_into_the_html(self):
        report = build_report(self.root)
        self.assertTrue(report.hotspots, "nothing ranked, so the test proves nothing")
        html = render_html(report)
        for name in self.NAMES:
            self.assertIn(name.rsplit("/", 1)[-1], html, name)

    def test_no_octal_escapes_leak_through(self):
        html = render_html(build_report(self.root))
        self.assertNotIn(r"\303", html)
        self.assertNotIn(r"\346", html)


class AwkwardRepositoryTests(unittest.TestCase):
    def test_repository_with_no_commits(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init", "-q", "-b", "main")
            report = build_report(root)
            self.assertEqual(report.total_commits, 0)
            self.assertEqual(report.authors, [])
            self.assertEqual(report.bus_factor, 0)
            # A report with nothing in it must still render.
            self.assertIn("</html>", render_html(report))

    def test_detached_head(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init", "-q", "-b", "main")
            for step in range(2):
                (root / "a.py").write_text(f"x = {step}\n")
                git(root, "add", "-A")
                git(root, "commit", "-qm", f"c{step}")
            git(root, "checkout", "-q", "HEAD~1")
            report = build_report(root)
            self.assertEqual(report.branch, "HEAD")
            self.assertEqual(report.total_commits, 1)

    def test_bare_repository_explains_itself(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, bare = root / "src", root / "bare.git"
            git(root, "init", "-q", "-b", "main", str(source))
            (source / "a.py").write_text("x = 1\n")
            git(source, "add", "-A")
            git(source, "commit", "-qm", "one")
            subprocess.run(["git", "clone", "-q", "--bare", str(source), str(bare)],
                           check=True, capture_output=True, env={**os.environ, **ENV})
            with self.assertRaises(GitError) as ctx:
                build_report(bare)
            self.assertIn("bare repository", str(ctx.exception))
            self.assertIn("working tree", str(ctx.exception))

    def test_a_file_path_resolves_to_its_repository(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init", "-q", "-b", "main")
            (root / "a.py").write_text("x = 1\n")
            git(root, "add", "-A")
            git(root, "commit", "-qm", "one")
            self.assertEqual(
                gitlog.repo_root(root / "a.py").resolve(), root.resolve()
            )

    def test_merge_commits_are_excluded_by_default(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init", "-q", "-b", "main")
            (root / "f.py").write_text("a\n")
            git(root, "add", "-A")
            git(root, "commit", "-qm", "base")
            git(root, "checkout", "-qb", "side")
            (root / "g.py").write_text("b\n")
            git(root, "add", "-A")
            git(root, "commit", "-qm", "side")
            git(root, "checkout", "-q", "main")
            git(root, "merge", "-q", "--no-ff", "side", "-m", "merge")

            self.assertEqual(build_report(root).total_commits, 2)
            self.assertEqual(build_report(root, include_merges=True).total_commits, 3)

    def test_deleted_files_do_not_appear(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init", "-q", "-b", "main")
            (root / "keep.py").write_text("x = 1\n")
            (root / "gone.py").write_text("y = 2\n")
            git(root, "add", "-A")
            git(root, "commit", "-qm", "one")
            (root / "gone.py").unlink()
            git(root, "add", "-A")
            git(root, "commit", "-qm", "remove")
            paths = {f.path for f in build_report(root).files}
            self.assertIn("keep.py", paths)
            self.assertNotIn("gone.py", paths)

    def test_untracked_files_are_ignored(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init", "-q", "-b", "main")
            (root / "tracked.py").write_text("x = 1\n")
            git(root, "add", "-A")
            git(root, "commit", "-qm", "one")
            (root / "scratch.py").write_text("junk = 1\n")
            paths = {f.path for f in build_report(root).files}
            self.assertEqual(paths, {"tracked.py"})


if __name__ == "__main__":
    unittest.main()

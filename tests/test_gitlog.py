import datetime as dt
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from blank import gitlog
from blank.gitlog import REC, SEP, Commit, FileChange


def sample(records):
    """Build log text in the exact format blank asks git for."""
    chunks = []
    for sha, author, email, when, subject, files in records:
        head = REC + SEP.join([sha, author, email, when, subject])
        body = "".join(f"\n{a}\t{d}\t{p}" for a, d, p in files)
        chunks.append(head + body)
    return "\n".join(chunks)


class ParseLogTests(unittest.TestCase):
    def test_parses_commit_and_numstat(self):
        text = sample([
            ("abc123", "Ada", "ada@example.com", "2024-03-01T10:00:00+00:00", "first", [("10", "2", "a.py")]),
        ])
        commits = list(gitlog.parse_log(text))
        self.assertEqual(len(commits), 1)
        commit = commits[0]
        self.assertEqual(commit.sha, "abc123")
        self.assertEqual(commit.author, "Ada")
        self.assertEqual(commit.email, "ada@example.com")
        self.assertEqual(commit.when.year, 2024)
        self.assertEqual(commit.files, [FileChange("a.py", 10, 2)])

    def test_subject_with_tabs_and_pipes_does_not_break_parsing(self):
        text = sample([
            ("s1", "Bo", "bo@x.io", "2024-03-02T10:00:00+00:00", "fix\tthing | and | more", [("1", "1", "b.py")]),
        ])
        commits = list(gitlog.parse_log(text))
        self.assertEqual(commits[0].subject, "fix\tthing | and | more")
        self.assertEqual(len(commits[0].files), 1)

    def test_binary_files_report_zero_lines(self):
        text = sample([("s", "A", "a@x", "2024-01-01T00:00:00+00:00", "bin", [("-", "-", "logo.png")])])
        change = list(gitlog.parse_log(text))[0].files[0]
        self.assertTrue(change.binary)
        self.assertEqual(change.churn, 0)

    def test_brace_rename_is_resolved(self):
        text = sample([
            ("s", "A", "a@x", "2024-01-01T00:00:00+00:00", "move", [("0", "0", "src/{old => new}/mod.py")]),
        ])
        change = list(gitlog.parse_log(text))[0].files[0]
        self.assertEqual(change.path, "src/new/mod.py")
        self.assertEqual(change.old_path, "src/old/mod.py")

    def test_simple_rename_is_resolved(self):
        text = sample([("s", "A", "a@x", "2024-01-01T00:00:00+00:00", "mv", [("0", "0", "a.py => b.py")])])
        change = list(gitlog.parse_log(text))[0].files[0]
        self.assertEqual((change.path, change.old_path), ("b.py", "a.py"))

    def test_paths_with_tabs_survive(self):
        text = sample([("s", "A", "a@x", "2024-01-01T00:00:00+00:00", "t", [("1", "0", "we\tird.py")])])
        self.assertEqual(list(gitlog.parse_log(text))[0].files[0].path, "we\tird.py")

    def test_empty_input_yields_nothing(self):
        self.assertEqual(list(gitlog.parse_log("")), [])
        self.assertEqual(list(gitlog.parse_log("\n\n")), [])

    def test_malformed_header_is_skipped(self):
        text = REC + "only-a-sha\n1\t1\ta.py"
        self.assertEqual(list(gitlog.parse_log(text)), [])


class RenameChainTests(unittest.TestCase):
    def make(self, path, old=None):
        return Commit("s", "A", "a@x", dt.datetime(2024, 1, 1), "m", [FileChange(path, 1, 0, old_path=old)])

    def test_chain_collapses_to_current_name(self):
        # git log is newest-first: c was renamed from b, which came from a.
        commits = [self.make("c.py", "b.py"), self.make("b.py", "a.py")]
        mapping = gitlog.follow_renames(commits)
        self.assertEqual(mapping["a.py"], "c.py")
        self.assertEqual(mapping["b.py"], "c.py")
        self.assertEqual(mapping["c.py"], "c.py")

    def test_cycle_does_not_hang(self):
        commits = [self.make("a.py", "b.py"), self.make("b.py", "a.py")]
        mapping = gitlog.follow_renames(commits)
        self.assertIn(mapping["a.py"], {"a.py", "b.py"})

    def test_log_command_shape(self):
        args = gitlog.log_command(since="2024-01-01", max_commits=50, include_merges=False)
        self.assertIn("--numstat", args)
        self.assertIn("--no-merges", args)
        self.assertIn("--since=2024-01-01", args)
        self.assertIn("-n50", args)
        self.assertNotIn("--no-merges", gitlog.log_command(since=None, max_commits=None, include_merges=True))


class RealRepoTests(unittest.TestCase):
    """End-to-end against a throwaway repository."""

    def test_reads_history_from_disk(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {"GIT_AUTHOR_NAME": "Ada", "GIT_AUTHOR_EMAIL": "ada@example.com",
                   "GIT_COMMITTER_NAME": "Ada", "GIT_COMMITTER_EMAIL": "ada@example.com"}
            run = lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True,  # noqa: E731
                                            capture_output=True, env={**__import__("os").environ, **env})
            run("init", "-q", "-b", "main")
            (root / "a.py").write_text("def f():\n    return 1\n")
            run("add", "-A")
            run("commit", "-qm", "add a")
            self.assertTrue(gitlog.has_commits(root))
            commits = gitlog.read_history(root)
            self.assertEqual(len(commits), 1)
            self.assertEqual(commits[0].files[0].path, "a.py")
            self.assertEqual(gitlog.repo_root(root).resolve(), root.resolve())

    def test_missing_path_raises(self):
        with self.assertRaises(gitlog.GitError):
            gitlog.repo_root(Path("/definitely/not/here"))


if __name__ == "__main__":
    unittest.main()

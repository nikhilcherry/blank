import datetime as dt
import unittest

from blank import analyze
from blank.analyze import AuthorStats, FileMetrics
from blank.gitlog import Commit, FileChange


def commit(paths, author="Ada", day=1, added=5, deleted=1):
    return Commit(
        sha=f"s{day}{author}{len(paths)}",
        author=author,
        email=f"{author.lower()}@example.com",
        when=dt.datetime(2024, 1, 1) + dt.timedelta(days=day),
        subject="change",
        files=[FileChange(p, added, deleted) for p in paths],
    )


class NormaliseTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(analyze._norm([]), [])

    def test_all_equal_values_score_zero(self):
        self.assertEqual(analyze._norm([7, 7, 7]), [0.0, 0.0, 0.0])

    def test_scaled_to_unit_range(self):
        scores = analyze._norm([1, 10, 100])
        self.assertAlmostEqual(scores[0], 0.0)
        self.assertAlmostEqual(scores[-1], 1.0)
        self.assertTrue(0 < scores[1] < 1)

    def test_log_scale_compresses_the_tail(self):
        # One 1000x outlier must not flatten the rest to zero the way a linear
        # scale would (there, 2 lands at 0.001).
        scores = analyze._norm([1, 2, 1000])
        self.assertGreater(scores[1], 0.05)
        self.assertGreater(scores[1], (2 - 1) / (1000 - 1) * 10)


class BusFactorTests(unittest.TestCase):
    def make(self, *line_counts):
        return [AuthorStats(name=f"a{i}", email=f"a{i}@x", added=n) for i, n in enumerate(line_counts)]

    def test_single_author_repo(self):
        self.assertEqual(analyze._bus_factor(self.make(100)), 1)

    def test_even_split_across_four(self):
        self.assertEqual(analyze._bus_factor(self.make(25, 25, 25, 25)), 2)

    def test_dominant_author(self):
        self.assertEqual(analyze._bus_factor(self.make(900, 50, 50)), 1)

    def test_no_contributions(self):
        self.assertEqual(analyze._bus_factor([]), 0)
        self.assertEqual(analyze._bus_factor(self.make(0, 0)), 0)


class CouplingTests(unittest.TestCase):
    def test_pairs_below_threshold_are_dropped(self):
        commits = [commit(["a.py", "b.py"], day=d) for d in range(2)]
        result = analyze._coupling(commits, {}, {"a.py", "b.py"})
        self.assertEqual(result, [])

    def test_consistent_pair_is_reported(self):
        commits = [commit(["a.py", "b.py"], day=d) for d in range(6)]
        result = analyze._coupling(commits, {}, {"a.py", "b.py"})
        self.assertEqual(len(result), 1)
        a, b, shared, ratio = result[0]
        self.assertEqual({a, b}, {"a.py", "b.py"})
        self.assertEqual(shared, 6)
        self.assertEqual(ratio, 1.0)

    def test_bulk_commits_do_not_create_coupling(self):
        paths = [f"f{i}.py" for i in range(analyze.BULK_COMMIT_FILES + 5)]
        commits = [commit(paths, day=d) for d in range(10)]
        self.assertEqual(analyze._coupling(commits, {}, set(paths)), [])

    def test_untracked_files_are_ignored(self):
        commits = [commit(["a.py", "gone.py"], day=d) for d in range(8)]
        self.assertEqual(analyze._coupling(commits, {}, {"a.py"}), [])

    def test_renames_are_followed_into_one_identity(self):
        commits = [commit(["old.py", "b.py"], day=d) for d in range(6)]
        result = analyze._coupling(commits, {"old.py": "new.py"}, {"new.py", "b.py"})
        self.assertEqual({result[0][0], result[0][1]}, {"new.py", "b.py"})


class PathRelationTests(unittest.TestCase):
    """Comparing first path segments was wrong in both directions."""

    def test_same_directory(self):
        self.assertEqual(
            analyze.path_relation("src/flask/app.py", "src/flask/cli.py"),
            analyze.SAME_DIRECTORY,
        )

    def test_root_level_files_are_the_same_directory(self):
        # The regression: a root-level file's first segment is its own
        # filename, so `flag_groups.go` and its test looked cross-module.
        # On cobra that mislabelled 22 of 40 pairs.
        self.assertEqual(
            analyze.path_relation("flag_groups.go", "flag_groups_test.go"),
            analyze.SAME_DIRECTORY,
        )

    def test_sibling_directories_are_the_same_area(self):
        # The other direction: two unrelated example apps shared a first
        # segment and so looked like one module.
        self.assertEqual(
            analyze.path_relation("examples/javascript/app.py", "examples/tutorial/app.py"),
            analyze.SAME_AREA,
        )

    def test_no_shared_directory_is_unrelated(self):
        self.assertEqual(
            analyze.path_relation("src/flask/ctx.py", "tests/test_session.py"),
            analyze.UNRELATED,
        )

    def test_root_file_against_nested_file_is_unrelated(self):
        self.assertEqual(
            analyze.path_relation("CHANGELOG.md", "crates/core/flags.rs"),
            analyze.UNRELATED,
        )

    def test_nested_but_diverging_early(self):
        self.assertEqual(
            analyze.path_relation("a/b/c/x.py", "a/z/y.py"), analyze.SAME_AREA
        )
        self.assertEqual(
            analyze.path_relation("a/b/c/x.py", "q/b/c/y.py"), analyze.UNRELATED
        )

    def test_is_symmetric(self):
        pairs = [
            ("src/a.py", "tests/b.py"),
            ("a.py", "b.py"),
            ("x/y/a.py", "x/z/b.py"),
            ("x/y/a.py", "x/y/b.py"),
        ]
        for a, b in pairs:
            self.assertEqual(analyze.path_relation(a, b), analyze.path_relation(b, a), (a, b))


class ActivityTests(unittest.TestCase):
    def test_window_is_fixed_length_and_ends_on_the_last_commit(self):
        commits = [commit(["a.py"], day=0), commit(["a.py"], day=3)]
        days = analyze._daily_activity(commits, days=10)
        self.assertEqual(len(days), 10)  # a fixed window, so the grid never jitters
        self.assertEqual(days[-1][0], dt.date(2024, 1, 4))
        self.assertEqual([count for _, count in days], [0, 0, 0, 0, 0, 0, 1, 0, 0, 1])

    def test_days_outside_the_window_are_dropped(self):
        commits = [commit(["a.py"], day=0), commit(["a.py"], day=60)]
        days = analyze._daily_activity(commits, days=7)
        self.assertEqual(sum(count for _, count in days), 1)

    def test_empty(self):
        self.assertEqual(analyze._daily_activity([]), [])


def metrics(path, *, commits, complexity, added=0, lines=100):
    return FileMetrics(
        path=path, language="Python", lines=lines, code_lines=lines,
        complexity=complexity, commits=commits, added=added,
    )


class HotspotScoringTests(unittest.TestCase):
    def test_a_file_committed_once_scores_zero(self):
        # Its "churn" is just its own length — that is creation, not change.
        files = [metrics("new.py", commits=1, complexity=9.0, added=5000)]
        analyze._score_hotspots(files)
        self.assertEqual(files[0].hotspot, 0.0)

    def test_a_freshly_imported_repo_ranks_nothing(self):
        files = [metrics(f"f{i}.py", commits=1, complexity=float(i + 1), added=900) for i in range(20)]
        analyze._score_hotspots(files)
        self.assertEqual({f.hotspot for f in files}, {0.0})

    def test_revisions_beat_churn(self):
        # A 200k-line vendored drop must not outrank a genuinely churning file
        # of the same complexity.
        files = [metrics(f"f{i}.py", commits=2 + i * 20, complexity=4.0 + i, added=100) for i in range(5)]
        big_once = metrics("vendored.py", commits=2, complexity=8.0, added=200_000)
        files.append(big_once)
        analyze._score_hotspots(files)
        self.assertEqual(max(files, key=lambda f: f.hotspot).path, "f4.py")
        self.assertGreater(files[4].hotspot, big_once.hotspot)

    def test_identical_complexity_carries_no_signal(self):
        # If every file is equally complex, complexity cannot discriminate, and
        # a relative score of 0 across the board is the honest answer.
        files = [metrics(f"f{i}.py", commits=2 + i * 30, complexity=5.0) for i in range(4)]
        analyze._score_hotspots(files)
        self.assertEqual({f.hotspot for f in files}, {0.0})

    def test_complexity_and_frequency_both_required(self):
        flat_but_busy = metrics("config.py", commits=300, complexity=0.0)
        gnarly_but_calm = metrics("legacy.py", commits=2, complexity=40.0)
        both = metrics("danger.py", commits=300, complexity=40.0)
        files = [flat_but_busy, gnarly_but_calm, both]
        analyze._score_hotspots(files)
        self.assertEqual(flat_but_busy.hotspot, 0.0)
        self.assertEqual(gnarly_but_calm.hotspot, 0.0)
        self.assertEqual(both.hotspot, 1.0)

    def test_scoring_is_idempotent(self):
        files = [metrics(f"f{i}.py", commits=i + 2, complexity=float(i + 1)) for i in range(6)]
        analyze._score_hotspots(files)
        first = [f.hotspot for f in files]
        analyze._score_hotspots(files)
        self.assertEqual(first, [f.hotspot for f in files])

    def test_empty_input(self):
        analyze._score_hotspots([])  # must not raise


class FileMetricsTests(unittest.TestCase):
    def test_ownership_and_main_author(self):
        record = FileMetrics("a.py", "Python", 10, 8, 1.0)
        record.authors.update({"Ada": 80, "Bo": 20})
        self.assertEqual(record.main_author, "Ada")
        self.assertAlmostEqual(record.ownership, 0.8)
        self.assertEqual(record.author_count, 2)

    def test_ownership_with_no_history(self):
        record = FileMetrics("a.py", "Python", 10, 8, 1.0)
        self.assertEqual(record.ownership, 0.0)
        self.assertEqual(record.main_author, "—")

    def test_age_days(self):
        now = dt.datetime(2024, 6, 1)
        record = FileMetrics("a.py", "Python", 1, 1, 0.0, last_seen=dt.datetime(2024, 5, 1))
        self.assertEqual(record.age_days(now), 31)
        self.assertIsNone(FileMetrics("b.py", "Python", 1, 1, 0.0).age_days(now))


if __name__ == "__main__":
    unittest.main()

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

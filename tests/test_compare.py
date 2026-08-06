import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from blank import compare
from blank.compare import CompareError


def payload(*, hotspots, commits=100, files=10, lines=1000, authors=3, bus=2, coupling=(), name="demo"):
    return {
        "tool": "blank",
        "version": "0.1.0",
        "repository": {"name": name, "branch": "main", "remote": None, "root": "/tmp/demo"},
        "generated": "2024-06-01T12:00:00",
        "window": {"since": None, "commits": commits,
                   "first_commit": "2024-01-01T00:00:00", "last_commit": "2024-06-01T00:00:00"},
        "summary": {"files": files, "lines": lines, "authors": authors,
                    "bus_factor": bus, "commits_per_week": 4.0},
        "languages": [], "authors": [], "knowledge_risk": [],
        "hotspots": [{"path": p, "risk": r} for p, r in hotspots],
        "coupling": [{"a": a, "b": b, "shared_commits": 5, "ratio": ratio} for a, b, ratio in coupling],
    }


class LoadTests(unittest.TestCase):
    def test_rejects_missing_file(self):
        with self.assertRaises(CompareError):
            compare.load("/definitely/not/here.json")

    def test_rejects_malformed_json(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{not json")
            with self.assertRaises(CompareError):
                compare.load(path)

    def test_rejects_json_that_is_not_a_blank_report(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "other.json"
            path.write_text('{"tool": "something-else"}')
            with self.assertRaises(CompareError) as ctx:
                compare.load(path)
            self.assertIn("not a blank report", str(ctx.exception))

    def test_round_trips_a_real_payload(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.json"
            path.write_text(json.dumps(payload(hotspots=[("a.py", 0.5)])))
            self.assertEqual(compare.load(path)["tool"], "blank")


class CompareTests(unittest.TestCase):
    def test_detects_each_direction(self):
        before = payload(hotspots=[("worse.py", 0.20), ("better.py", 0.80), ("gone.py", 0.40)])
        after = payload(hotspots=[("worse.py", 0.60), ("better.py", 0.30), ("new.py", 0.50)])
        diff = compare.compare(before, after)
        self.assertEqual([d.path for d in diff.worse], ["worse.py"])
        self.assertEqual([d.path for d in diff.better], ["better.py"])
        self.assertEqual([d.path for d in diff.added], ["new.py"])
        self.assertEqual([d.path for d in diff.removed], ["gone.py"])

    def test_noise_floor_suppresses_small_moves(self):
        before = payload(hotspots=[("a.py", 0.50)])
        after = payload(hotspots=[("a.py", 0.52)])
        self.assertTrue(compare.compare(before, after).unchanged)
        self.assertFalse(compare.compare(before, after, floor=0.001).unchanged)

    def test_worst_increase_counts_new_hotspots(self):
        before = payload(hotspots=[("a.py", 0.10)])
        after = payload(hotspots=[("a.py", 0.20), ("brand-new.py", 0.90)])
        diff = compare.compare(before, after)
        self.assertAlmostEqual(diff.worst_increase, 0.90)
        self.assertEqual(diff.regressed[0].path, "brand-new.py")

    def test_regressed_is_sorted_by_size_of_increase(self):
        before = payload(hotspots=[("a.py", 0.10), ("b.py", 0.10)])
        after = payload(hotspots=[("a.py", 0.30), ("b.py", 0.70)])
        self.assertEqual([d.path for d in compare.compare(before, after).regressed], ["b.py", "a.py"])

    def test_summary_deltas(self):
        before = payload(hotspots=[], commits=100, files=10, lines=1000, authors=3, bus=1)
        after = payload(hotspots=[], commits=150, files=12, lines=1400, authors=5, bus=3)
        diff = compare.compare(before, after)
        self.assertEqual(diff.commits, (100, 150))
        self.assertEqual(diff.bus_factor, (1, 3))
        self.assertTrue(diff.unchanged)

    def test_new_coupling_only_reports_pairs_that_are_new(self):
        before = payload(hotspots=[], coupling=[("a.py", "b.py", 0.9)])
        after = payload(hotspots=[], coupling=[("a.py", "b.py", 0.9), ("c.py", "d.py", 0.8)])
        self.assertEqual([(a, b) for a, b, _ in compare.compare(before, after).new_coupling],
                         [("c.py", "d.py")])

    def test_coupling_pair_order_does_not_matter(self):
        before = payload(hotspots=[], coupling=[("b.py", "a.py", 0.9)])
        after = payload(hotspots=[], coupling=[("a.py", "b.py", 0.9)])
        self.assertEqual(compare.compare(before, after).new_coupling, [])

    def test_a_rename_reads_as_removed_plus_added(self):
        # Two independent analyses cannot see across a rename, and saying so is
        # more honest than guessing which new file replaced which old one.
        before = payload(hotspots=[("src/app.py", 1.0)])
        after = payload(hotspots=[("src/sansio/app.py", 1.0)])
        diff = compare.compare(before, after)
        self.assertEqual([d.path for d in diff.removed], ["src/app.py"])
        self.assertEqual([d.path for d in diff.added], ["src/sansio/app.py"])


class MarkdownTests(unittest.TestCase):
    def test_renders_sections(self):
        before = payload(hotspots=[("worse.py", 0.20), ("gone.py", 0.40)])
        after = payload(hotspots=[("worse.py", 0.60), ("new.py", 0.50)],
                        coupling=[("x.py", "y.py", 0.7)])
        md = compare.render_markdown(compare.compare(before, after))
        self.assertIn("## `demo` — what moved", md)
        self.assertIn("Got riskier", md)
        self.assertIn("New hotspots", md)
        self.assertIn("No longer ranked", md)
        self.assertIn("Newly coupled", md)
        self.assertIn("`worse.py`", md)

    def test_says_so_when_nothing_moved(self):
        same = payload(hotspots=[("a.py", 0.5)])
        md = compare.render_markdown(compare.compare(same, same))
        self.assertIn("Nothing to report", md)
        self.assertNotIn("Got riskier", md)


if __name__ == "__main__":
    unittest.main()

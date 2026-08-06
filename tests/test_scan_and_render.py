import datetime as dt
import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from blank import charts, scan
from blank.analyze import AuthorStats, FileMetrics, Report
from blank.render import render_html, render_json


class ClassifyTests(unittest.TestCase):
    def test_extensions(self):
        self.assertEqual(scan.classify("src/main.py"), "Python")
        self.assertEqual(scan.classify("web/app.tsx"), "TypeScript")
        self.assertEqual(scan.classify("go/main.go"), "Go")

    def test_bare_filenames(self):
        self.assertEqual(scan.classify("Dockerfile"), "Docker")
        self.assertEqual(scan.classify("ops/Dockerfile.prod"), "Docker")
        self.assertEqual(scan.classify("Makefile"), "Make")

    def test_unknown_falls_through(self):
        self.assertEqual(scan.classify("data.qqq"), "Other")

    def test_skips(self):
        self.assertTrue(scan.should_skip("node_modules/pkg/index.js"))
        self.assertTrue(scan.should_skip("app/bundle.min.js"))
        self.assertTrue(scan.should_skip("assets/logo.png"))
        self.assertFalse(scan.should_skip("src/app.js"))


class MeasureTests(unittest.TestCase):
    def test_indentation_is_measured_in_four_space_units(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.py"
            path.write_text("def f():\n    if x:\n        return 1\n\n")
            lines, code, mean, mx, binary = scan.measure(path)
            self.assertFalse(binary)
            self.assertEqual(lines, 4)
            self.assertEqual(code, 3)   # blank line excluded
            self.assertEqual(mx, 2)
            self.assertAlmostEqual(mean, 1.0)

    def test_tabs_count_as_one_unit(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.go"
            path.write_text("func f() {\n\treturn\n}\n")
            *_, mx, _ = scan.measure(path)
            self.assertEqual(mx, 1)

    def test_binary_detected(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.bin"
            path.write_bytes(b"\x00\x01\x02")
            self.assertTrue(scan.measure(path)[4])

    def test_complexity_needs_enough_lines(self):
        tiny = scan.FileInfo("a.py", "Python", 10, 3, 3.0, 2.0, 8)
        self.assertEqual(tiny.complexity, 0.0)
        real = scan.FileInfo("a.py", "Python", 400, 40, 30, 2.0, 8)
        self.assertGreater(real.complexity, 0)


class ScanTreeTests(unittest.TestCase):
    def test_walks_and_filters(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "node_modules" / "dep").mkdir(parents=True)
            (root / "src" / "app.py").write_text("x = 1\n")
            (root / "node_modules" / "dep" / "i.js").write_text("var x\n")
            (root / "logo.png").write_bytes(b"\x89PNG\r\n")
            found = {f.path for f in scan.scan_tree(root)}
            self.assertEqual(found, {"src/app.py"})

    def test_tracked_filter_excludes_untracked(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("x = 1\n")
            (root / "b.py").write_text("y = 2\n")
            found = {f.path for f in scan.scan_tree(root, {"a.py"})}
            self.assertEqual(found, {"a.py"})


class ChartTests(unittest.TestCase):
    def test_heatmap_renders_a_cell_per_day(self):
        days = [(dt.date(2024, 1, 1) + dt.timedelta(days=i), i % 5) for i in range(30)]
        svg = charts.heatmap(days)
        self.assertEqual(svg.count("<rect"), 30)
        self.assertIn("</svg>", svg)

    def test_heatmap_empty(self):
        self.assertIn("No commit activity", charts.heatmap([]))

    def test_donut_slices_cover_the_circle(self):
        svg = charts.donut([("Python", 60), ("Go", 40)])
        self.assertEqual(svg.count("<circle"), 2)
        lengths = [float(m) for m in re.findall(r'stroke-dasharray="([0-9.]+)', svg)]
        self.assertAlmostEqual(sum(lengths), 2 * 3.141592653589793 * (190 / 2 - 30 / 2 - 2), places=2)

    def test_donut_ignores_zero_total(self):
        self.assertIn("Nothing to chart", charts.donut([("a", 0)]))

    def test_treemap_rectangles_fill_the_canvas(self):
        items = [(f"d{i}", float(100 - i * 8), f"d{i}") for i in range(10)]
        svg = charts.treemap(items, width=400, height=200)
        areas = [
            float(w) * float(h)
            for w, h in re.findall(r'width="([0-9.]+)" height="([0-9.]+)"', svg)
        ]
        # Rectangles are inset by 2px each, so expect a little under the full area.
        self.assertGreater(sum(areas), 400 * 200 * 0.75)
        self.assertLess(sum(areas), 400 * 200)

    def test_squarify_layout_stays_in_bounds(self):
        rects = charts._squarify([50, 30, 12, 8], 0, 0, 300, 150)
        self.assertEqual(len(rects), 4)
        for x, y, w, h in rects:
            self.assertGreaterEqual(round(x, 6), 0)
            self.assertGreaterEqual(round(y, 6), 0)
            self.assertLessEqual(round(x + w, 4), 300.0001)
            self.assertLessEqual(round(y + h, 4), 150.0001)

    def test_scatter_and_bars_handle_empty(self):
        self.assertIn("Not enough history", charts.scatter([]))
        self.assertIn("No data", charts.bars([]))

    def test_escaping_blocks_injection(self):
        svg = charts.treemap([("<script>x</script>", 10.0, "<b>tip</b>")])
        self.assertNotIn("<script>", svg)
        self.assertIn("&lt;", svg)


def make_report():
    files = []
    for i in range(6):
        record = FileMetrics(
            path=f"src/mod{i}.py", language="Python", lines=100 + i * 10,
            code_lines=80, complexity=1.0 + i, commits=i + 1,
            added=50 * (i + 1), deleted=10 * i,
            first_seen=dt.datetime(2024, 1, 1), last_seen=dt.datetime(2024, 6, 1),
        )
        record.authors.update({"Ada": 30, "Bo": 5})
        record.hotspot = round(i / 6, 3)
        files.append(record)
    authors = [
        AuthorStats("Ada", "ada@x", commits=40, added=900, deleted=100,
                    files={"src/mod1.py"}, first=dt.datetime(2024, 1, 1), last=dt.datetime(2024, 6, 1)),
        AuthorStats("Bo", "bo@x", commits=8, added=120, deleted=30,
                    files={"src/mod2.py"}, first=dt.datetime(2024, 2, 1), last=dt.datetime(2024, 5, 1)),
    ]
    return Report(
        name="demo", root="/tmp/demo", branch="main", remote="git@example.com:demo.git",
        generated=dt.datetime(2024, 6, 2, 12, 0), version="0.1.0",
        files=files, authors=authors,
        languages=[("Python", 6, 660)],
        activity=[(dt.date(2024, 5, 1) + dt.timedelta(days=i), i % 4) for i in range(40)],
        coupling=[("src/mod1.py", "src/mod2.py", 9, 0.9)],
        directories=[("src", 660, 6)],
        orphans=[files[-1]], stale=[],
        total_commits=48, window_commits=48,
        first_commit=dt.datetime(2024, 1, 1), last_commit=dt.datetime(2024, 6, 1),
        bus_factor=1, since=None, skipped_bulk=0,
    )


class RenderTests(unittest.TestCase):
    def test_html_is_self_contained(self):
        html = render_html(make_report())
        self.assertTrue(html.startswith("<!doctype html>"))
        self.assertIn("<style>", html)
        self.assertIn("<script>", html)
        # No external references of any kind.
        self.assertNotIn("http://", html.replace("http://www.w3.org", ""))
        self.assertNotIn("src=\"http", html)
        self.assertNotIn("<link", html)

    def test_html_contains_the_data(self):
        html = render_html(make_report())
        self.assertIn("demo", html)
        self.assertIn("src/mod5.py", html)
        self.assertIn("Bus factor", html)
        self.assertIn("Ada", html)

    def test_json_round_trips(self):
        payload = json.loads(render_json(make_report()))
        self.assertEqual(payload["tool"], "blank")
        self.assertEqual(payload["summary"]["bus_factor"], 1)
        self.assertEqual(payload["repository"]["branch"], "main")
        self.assertEqual(payload["hotspots"][0]["path"], "src/mod5.py")
        self.assertEqual(payload["coupling"][0]["shared_commits"], 9)

    def test_report_derived_properties(self):
        report = make_report()
        self.assertEqual(report.total_lines, sum(f.lines for f in report.files))
        self.assertEqual(report.hotspots[0].path, "src/mod5.py")
        self.assertGreater(report.commits_per_week, 0)


if __name__ == "__main__":
    unittest.main()

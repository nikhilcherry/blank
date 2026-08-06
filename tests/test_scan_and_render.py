import datetime as dt
import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from blank import charts, scan
from blank.analyze import AuthorStats, FileMetrics, Report
from blank.render import render_html, render_json, render_markdown


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

    def test_translation_catalogs_are_named_not_dumped_into_other(self):
        # Django is a third .po by line count; leaving that in "Other" makes
        # the composition chart useless.
        self.assertEqual(scan.classify("locale/de/LC_MESSAGES/django.po"), "Gettext")
        self.assertIn("Gettext", scan.NON_CODE)

    def test_non_code_languages_are_excluded_from_ranking(self):
        for language in ("Markdown", "JSON", "YAML", "Gettext", "Other"):
            self.assertIn(language, scan.NON_CODE, language)
        for language in ("Python", "Go", "Rust", "TypeScript"):
            self.assertNotIn(language, scan.NON_CODE, language)

    def test_skips(self):
        self.assertTrue(scan.should_skip("node_modules/pkg/index.js"))
        self.assertTrue(scan.should_skip("app/bundle.min.js"))
        self.assertTrue(scan.should_skip("assets/logo.png"))
        self.assertFalse(scan.should_skip("src/app.js"))


class IndentUnitTests(unittest.TestCase):
    """One nesting level is not always four columns."""

    def test_detects_four(self):
        self.assertEqual(scan.detect_indent_unit([0, 4, 8, 4, 0, 4, 8, 12]), 4)

    def test_detects_two(self):
        # JavaScript, TypeScript and Ruby overwhelmingly indent in twos.
        self.assertEqual(scan.detect_indent_unit([0, 2, 4, 2, 0, 2, 4, 6]), 2)

    def test_detects_three(self):
        self.assertEqual(scan.detect_indent_unit([0, 3, 6, 3, 0, 3, 6, 9]), 3)

    def test_flat_file_falls_back(self):
        self.assertEqual(scan.detect_indent_unit([0, 0, 0]), scan.TAB_WIDTH)
        self.assertEqual(scan.detect_indent_unit([]), scan.TAB_WIDTH)

    def test_giant_steps_are_alignment_not_nesting(self):
        # Continuation lines aligned under a long call signature.
        self.assertEqual(scan.detect_indent_unit([0, 40, 0, 40]), scan.TAB_WIDTH)

    def test_never_returns_zero_or_negative(self):
        for widths in ([0, 0], [5, 1, 5, 1], [0], [3, 3, 3]):
            self.assertGreaterEqual(scan.detect_indent_unit(widths), 1, widths)


class MeasureTests(unittest.TestCase):
    def test_depth_is_measured_in_nesting_levels(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.py"
            path.write_text("def f():\n    if x:\n        return 1\n\n")
            lines, code, mean, mx, binary = scan.measure(path)
            self.assertFalse(binary)
            self.assertEqual(lines, 4)
            self.assertEqual(code, 3)   # blank line excluded
            self.assertEqual(mx, 2)
            self.assertAlmostEqual(mean, 1.0)

    def test_two_space_and_four_space_code_measure_the_same(self):
        """The whole point: identical nesting must score identically.

        Before per-file unit detection, the 2-space version scored half as
        deep, so JavaScript could never outrank Python however tangled it got.
        """
        four = "def f():\n    if x:\n        if y:\n            return 1\n"
        two = "function f() {\n  if (x) {\n    if (y) {\n      return 1;\n"
        with TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.py", Path(tmp) / "b.js"
            a.write_text(four)
            b.write_text(two)
            _, _, mean_a, max_a, _ = scan.measure(a)
            _, _, mean_b, max_b, _ = scan.measure(b)
            self.assertEqual((mean_a, max_a), (mean_b, max_b))
            self.assertEqual(max_a, 3)

    def test_tabs_count_as_one_level(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.go"
            path.write_text("func f() {\n\treturn\n}\n")
            *_, mx, _ = scan.measure(path)
            self.assertEqual(mx, 1)

    def test_tab_and_space_files_agree(self):
        tabbed = "func f() {\n\tif x {\n\t\treturn 1\n\t}\n}\n"
        spaced = "func f() {\n    if x {\n        return 1\n    }\n}\n"
        with TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.go", Path(tmp) / "b.go"
            a.write_text(tabbed)
            b.write_text(spaced)
            self.assertEqual(scan.measure(a), scan.measure(b))

    def test_empty_file(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.py"
            path.write_text("")
            self.assertEqual(scan.measure(path), (0, 0, 0.0, 0, False))

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

    def test_markdown_is_a_compact_summary(self):
        md = render_markdown(make_report())
        self.assertTrue(md.startswith("## `demo`"))
        self.assertIn("| File | Risk | Revisions |", md)
        self.assertIn("`src/mod5.py`", md)
        self.assertIn("bus factor **1**", md)
        # Table rows must not leak beyond the limit.
        self.assertLessEqual(len(render_markdown(make_report(), limit=2).splitlines()), 30)

    def test_markdown_flags_a_low_bus_factor(self):
        self.assertIn("bus factor **1** ⚠️", render_markdown(make_report()))

    def test_markdown_escapes_nothing_it_should_not(self):
        md = render_markdown(make_report())
        # Paths are wrapped in backticks, so underscores stay literal.
        self.assertIn("`src/mod1.py` ⇄ `src/mod2.py`", md)

    def test_markdown_reports_thin_history(self):
        report = make_report()
        for record in report.files:
            record.commits = 1
        self.assertIn("Thin history", render_markdown(report))

    def test_overflow_languages_merge_into_the_existing_other_slice(self):
        from blank.render import _language_card
        report = make_report()
        # "Other" is both a real bucket and the overflow label — one slice only.
        report.languages = (
            [(f"Lang{i}", 1, 1000 - i * 10) for i in range(8)]
            + [("Other", 1, 500)]
            + [(f"Tail{i}", 1, 5) for i in range(4)]
        )
        report.languages.sort(key=lambda row: row[2], reverse=True)
        card = _language_card(report)
        self.assertEqual(card.count(">Other<"), 1)

    def test_scatter_sampling_keeps_the_low_risk_tail(self):
        from blank.render import _SCATTER_POINTS, _scatter_card
        report = make_report()
        report.files = []
        for i in range(_SCATTER_POINTS * 4):
            record = FileMetrics(f"f{i}.py", "Python", 100, 90, 1.0 + (i % 40) / 4, commits=2 + i)
            record.hotspot = round(i / (_SCATTER_POINTS * 4), 4)
            report.files.append(record)
        svg, note = _scatter_card(report)
        self.assertIn("riskiest files plus every", note)
        # Both extremes must survive the cut, or the chart lies about the shape.
        self.assertIn("cool", svg)
        self.assertIn("hot", svg)

    def test_scatter_does_not_annotate_when_it_shows_everything(self):
        from blank.render import _scatter_card
        svg, note = _scatter_card(make_report())
        self.assertEqual(note, "")
        self.assertIn("<svg", svg)

    def test_ordinal(self):
        from blank.render import _ordinal
        self.assertEqual(
            [_ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 101, 111)],
            ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "101st", "111th"],
        )

    def test_report_derived_properties(self):
        report = make_report()
        self.assertEqual(report.total_lines, sum(f.lines for f in report.files))
        self.assertEqual(report.hotspots[0].path, "src/mod5.py")
        self.assertGreater(report.commits_per_week, 0)


if __name__ == "__main__":
    unittest.main()

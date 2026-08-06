"""Terminal rendering: colour, bars, sparklines, tables.

Colour is switched off automatically when stdout is not a TTY, when ``NO_COLOR``
is set, or when ``TERM=dumb`` — so piping into a file or a CI log stays clean.
"""

from __future__ import annotations

import datetime as _dt
import os
import shutil
import sys
import unicodedata
from collections.abc import Sequence

from .analyze import Report

SPARK = "▁▂▃▄▅▆▇█"
BLOCK = "█"
SHADE = "░"


class Style:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def bold(self, t: str) -> str: return self._wrap("1", t)
    def dim(self, t: str) -> str: return self._wrap("2", t)
    def red(self, t: str) -> str: return self._wrap("31", t)
    def green(self, t: str) -> str: return self._wrap("32", t)
    def yellow(self, t: str) -> str: return self._wrap("33", t)
    def blue(self, t: str) -> str: return self._wrap("34", t)
    def orange(self, t: str) -> str: return self._wrap("38;5;208", t)
    def cyan(self, t: str) -> str: return self._wrap("36", t)


def make_style(force: bool | None = None) -> Style:
    if force is not None:
        return Style(force)
    if os.environ.get("NO_COLOR") is not None:
        return Style(False)
    if os.environ.get("TERM") == "dumb":
        return Style(False)
    return Style(sys.stdout.isatty())


def width(default: int = 92) -> int:
    try:
        return max(60, min(shutil.get_terminal_size().columns, 120))
    except OSError:  # pragma: no cover
        return default


def sparkline(values: Sequence[float]) -> str:
    if not values:
        return ""
    peak = max(values)
    if peak <= 0:
        return SPARK[0] * len(values)
    return "".join(SPARK[min(len(SPARK) - 1, int(v / peak * (len(SPARK) - 1) + 0.5))] for v in values)


def bar(value: float, peak: float, cells: int) -> str:
    if peak <= 0:
        return SHADE * cells
    filled = int(round(value / peak * cells))
    return BLOCK * filled + SHADE * (cells - filled)


def display_width(text: str) -> int:
    """Columns *text* occupies in a terminal.

    ``len()`` is wrong twice over: combining marks (the ¨ in a decomposed
    "Neuhäuser") take no width, and CJK characters take two. Author names hit
    both cases, so every column would drift without this.
    """
    total = 0
    for ch in unicodedata.normalize("NFC", text):
        if unicodedata.combining(ch):
            continue
        total += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return total


def truncate(text: str, limit: int) -> str:
    """Shorten from the left — the tail of a path is the informative part."""
    text = unicodedata.normalize("NFC", text)
    if display_width(text) <= limit:
        return text
    out = ""
    for ch in reversed(text):
        if display_width(out) + display_width(ch) > limit - 1:
            break
        out = ch + out
    return "…" + out


def pad(text: str, cells: int) -> str:
    """Left-align *text* in *cells* terminal columns."""
    return text + " " * max(0, cells - display_width(text))


def _num(value: float) -> str:
    return f"{value:,.0f}"


def _rule(style: Style, title: str, cols: int) -> str:
    label = f"── {title} "
    return style.dim(label + "─" * max(0, cols - len(label)))


def weekly_buckets(report: Report, weeks: int = 26) -> list[int]:
    if not report.activity:
        return []
    counts = [count for _, count in report.activity]
    tail = counts[-(weeks * 7):]
    return [sum(tail[i:i + 7]) for i in range(0, len(tail), 7)]


def render_summary(report: Report, style: Style) -> str:
    cols = width()
    out: list[str] = []
    now = report.last_commit or _dt.datetime.now(_dt.timezone.utc)

    header = f"{style.bold(report.name)} {style.dim('·')} {style.dim(report.branch)}"
    out.append("")
    out.append(header)
    span = (
        f"{report.first_commit:%b %Y} – {report.last_commit:%b %Y}"
        if report.first_commit and report.last_commit
        else "no history"
    )
    out.append(
        style.dim(
            f"{_num(report.total_commits)} commits · {_num(len(report.files))} files · "
            f"{_num(report.total_lines)} lines · {span}"
        )
    )
    out.append("")

    bus_colour = style.red if report.bus_factor <= 1 else (style.yellow if report.bus_factor <= 2 else style.green)
    active = sum(1 for a in report.authors if a.active_days_ago(now) <= 90)
    out.append(
        "  "
        + style.bold("bus factor ")
        + bus_colour(str(report.bus_factor))
        + style.dim(f"  ({len(report.authors)} contributors, {active} active in 90d)")
    )
    out.append("  " + style.bold("commit rate ") + f"{report.commits_per_week}" + style.dim("/week"))

    weekly = weekly_buckets(report)
    if weekly:
        out.append("  " + style.bold("last 26 weeks ") + style.orange(sparkline(weekly)))
    out.append("")

    langs = report.languages[:6]
    if langs:
        out.append(_rule(style, "languages", cols))
        peak = max(lines for _, _, lines in langs)
        for lang, files, lines in langs:
            out.append(
                f"  {pad(lang[:16], 16)} {style.cyan(bar(lines, peak, 24))} "
                f"{_num(lines):>8} lines {style.dim(f'({files} files)')}"
            )
        out.append("")

    hotspots = report.hotspots[:10]
    if report.code_files:
        out.append(_rule(style, "hotspots  (revisions × complexity)", cols))
        if report.thin_history:
            out.append(
                "  "
                + style.yellow("thin history")
                + style.dim(
                    f" — only {report.scored_files} file(s) revised more than once,"
                    " so the ranking is noise"
                )
            )
        if hotspots:
            path_width = max(24, cols - 46)
            out.append(
                style.dim(f"  {'file':<{path_width}} {'risk':>5} {'commits':>8} {'churn':>8} {'lines':>7}")
            )
            for record in hotspots:
                colour = (
                    style.red if record.hotspot >= 0.5
                    else (style.yellow if record.hotspot >= 0.22 else style.green)
                )
                out.append(
                    f"  {pad(truncate(record.path, path_width), path_width)} "
                    f"{colour(f'{record.hotspot:>5.2f}')} "
                    f"{_num(record.commits):>8} {_num(record.churn):>8} {_num(record.lines):>7}"
                )
        else:
            out.append(style.dim("  nothing to rank — no file has been revised since it was created."))
        out.append("")

    if report.coupling:
        out.append(_rule(style, "temporal coupling", cols))
        for a, b, shared, ratio in report.coupling[:5]:
            half = max(16, (cols - 26) // 2)
            out.append(
                f"  {pad(truncate(a, half), half)} {style.orange('⇄')} {pad(truncate(b, half), half)} "
                + style.dim(f"{ratio * 100:.0f}% / {shared}×")
            )
        out.append("")

    if report.orphans:
        out.append(_rule(style, "knowledge risk", cols))
        for record in report.orphans[:5]:
            out.append(
                f"  {style.red('!')} {pad(truncate(record.path, cols - 40), max(20, cols - 40))} "
                + style.dim(f"{record.ownership * 100:.0f}% {truncate(record.main_author, 18)}")
            )
        out.append("")

    authors = report.authors[:8]
    if authors:
        out.append(_rule(style, "contributors", cols))
        peak = max(a.commits for a in authors)
        for author in authors:
            days = author.active_days_ago(now)
            recency = style.dim(f"{days}d ago") if days < 10**5 else ""
            out.append(
                f"  {pad(truncate(author.name, 20), 20)} {style.blue(bar(author.commits, peak, 20))} "
                f"{_num(author.commits):>6} commits  {recency}"
            )
        out.append("")

    return "\n".join(out)


def render_hotspots(report: Report, style: Style, limit: int) -> str:
    cols = width()
    rows = report.hotspots[:limit]
    if not rows:
        return style.dim("No hotspots — not enough history to score files.")
    path_width = max(24, cols - 58)
    out = [
        style.dim(
            f"  {'file':<{path_width}} {'risk':>5} {'commits':>8} {'churn':>8} "
            f"{'cx':>5} {'lines':>7} {'auth':>5}"
        )
    ]
    for record in rows:
        colour = style.red if record.hotspot >= 0.5 else (style.yellow if record.hotspot >= 0.22 else style.green)
        out.append(
            f"  {pad(truncate(record.path, path_width), path_width)} "
            f"{colour(f'{record.hotspot:>5.2f}')} "
            f"{_num(record.commits):>8} {_num(record.churn):>8} "
            f"{record.complexity:>5.1f} {_num(record.lines):>7} {record.author_count:>5}"
        )
    return "\n".join(out)


def render_authors(report: Report, style: Style, limit: int) -> str:
    now = report.last_commit or _dt.datetime.now(_dt.timezone.utc)
    rows = report.authors[:limit]
    if not rows:
        return style.dim("No authors found.")
    peak = max(a.commits for a in rows)
    out = []
    for author in rows:
        days = author.active_days_ago(now)
        out.append(
            f"  {pad(truncate(author.name, 22), 22)} {style.blue(bar(author.commits, peak, 20))} "
            f"{_num(author.commits):>6} commits  "
            + style.green(f"+{_num(author.added):<8}")
            + style.red(f"-{_num(author.deleted):<8}")
            + style.dim(f"{len(author.files)} files · {days}d ago")
        )
    return "\n".join(out)


def render_coupling(report: Report, style: Style, limit: int) -> str:
    cols = width()
    if not report.coupling:
        return style.dim("No file pairs cross the coupling threshold.")
    half = max(16, (cols - 26) // 2)
    out = []
    for a, b, shared, ratio in report.coupling[:limit]:
        cross = a.split("/")[0] != b.split("/")[0]
        marker = style.orange("⇄") if cross else style.dim("⇄")
        out.append(
            f"  {pad(truncate(a, half), half)} {marker} {pad(truncate(b, half), half)} "
            + style.dim(f"{ratio * 100:>3.0f}% / {shared}×")
        )
    return "\n".join(out)

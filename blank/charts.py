"""SVG chart builders.

Charts are generated server-side as plain SVG strings. No charting library, no
CDN, no client-side rendering — the report opens from a file:// URL on a plane
and still draws. Colours come from CSS custom properties so light and dark
themes are one stylesheet away.
"""

from __future__ import annotations

import datetime as _dt
import html
import math
from collections.abc import Sequence

CATEGORICAL = 8  # number of `--cat-N` slots defined in the stylesheet


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _fmt(value: float) -> str:
    """Compact number formatting: 1234567 -> 1.2M."""
    number = float(value)
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(number) >= limit:
            trimmed = f"{number / limit:.1f}".rstrip("0").rstrip(".")
            return f"{trimmed}{suffix}"
    return f"{number:.0f}"


# --------------------------------------------------------------------------
# Activity heatmap
# --------------------------------------------------------------------------

def heatmap(days: Sequence[tuple[_dt.date, int]], *, cell: int = 11, gap: int = 3) -> str:
    """GitHub-style contribution grid. Weeks run left to right, Mon..Sun down."""
    if not days:
        return '<p class="empty">No commit activity in range.</p>'

    counts = [count for _, count in days]
    peak = max(counts) or 1
    # Quartile-ish thresholds against the non-zero distribution so a repo with
    # one 40-commit day does not render every other day as level 1.
    active = sorted(c for c in counts if c > 0)
    if active:
        q = lambda p: active[min(len(active) - 1, int(len(active) * p))]  # noqa: E731
        stops = [q(0.25), q(0.55), q(0.8)]
    else:
        stops = [1, 2, 3]

    def level(count: int) -> int:
        if count <= 0:
            return 0
        if count <= stops[0]:
            return 1
        if count <= stops[1]:
            return 2
        if count <= stops[2]:
            return 3
        return 4

    first = days[0][0]
    lead = first.weekday()  # Monday == 0
    total_cells = lead + len(days)
    weeks = math.ceil(total_cells / 7)

    pad_left, pad_top = 30, 20
    width = pad_left + weeks * (cell + gap)
    height = pad_top + 7 * (cell + gap) + 6

    parts = [
        f'<svg class="heatmap" viewBox="0 0 {width} {height}" width="100%" '
        f'height="{height}" role="img" aria-label="Commit activity heatmap">'
    ]

    # One label per month, anchored to the first week containing that month's
    # first days. Keying by week instead would print "AugAug" whenever two
    # adjacent weeks both hold days 1-7.
    months: dict[tuple[int, int], tuple[int, str]] = {}
    for index, (day, count) in enumerate(days):
        pos = lead + index
        week, weekday = divmod(pos, 7)
        x = pad_left + week * (cell + gap)
        y = pad_top + weekday * (cell + gap)
        title = f"{day.isoformat()} — {count} commit{'' if count == 1 else 's'}"
        parts.append(
            f'<rect class="hm l{level(count)}" x="{x}" y="{y}" width="{cell}" height="{cell}" '
            f'rx="2"><title>{esc(title)}</title></rect>'
        )
        if day.day <= 7:
            months.setdefault((day.year, day.month), (week, day.strftime("%b")))

    for week, label in months.values():
        x = pad_left + week * (cell + gap)
        parts.append(f'<text class="axis" x="{x}" y="{pad_top - 7}">{label}</text>')
    for row, label in ((0, "Mon"), (2, "Wed"), (4, "Fri")):
        y = pad_top + row * (cell + gap) + cell - 1
        parts.append(f'<text class="axis" x="0" y="{y}">{label}</text>')

    parts.append("</svg>")
    parts.append(
        '<div class="legend"><span>Less</span>'
        + "".join(f'<i class="hm-key l{n}"></i>' for n in range(5))
        + f"<span>More</span><span class=\"peak\">peak {peak}/day</span></div>"
    )
    return "".join(parts)


# --------------------------------------------------------------------------
# Donut
# --------------------------------------------------------------------------

def donut(slices: Sequence[tuple[str, float]], *, size: int = 190, thickness: int = 30) -> str:
    """Ring chart. *slices* is ``(label, value)``, already sorted."""
    total = sum(max(0.0, value) for _, value in slices)
    if total <= 0:
        return '<p class="empty">Nothing to chart.</p>'

    radius = size / 2 - thickness / 2 - 2
    centre = size / 2
    circumference = 2 * math.pi * radius
    parts = [
        f'<svg class="donut" viewBox="0 0 {size} {size}" width="{size}" height="{size}" '
        f'role="img" aria-label="Composition">'
    ]
    offset = 0.0
    for index, (label, value) in enumerate(slices):
        fraction = max(0.0, value) / total
        if fraction <= 0:
            continue
        length = fraction * circumference
        parts.append(
            f'<circle class="arc c{index % CATEGORICAL}" cx="{centre}" cy="{centre}" r="{radius:.2f}" '
            f'fill="none" stroke-width="{thickness}" '
            f'stroke-dasharray="{length:.3f} {circumference - length:.3f}" '
            f'stroke-dashoffset="{-offset:.3f}" transform="rotate(-90 {centre} {centre})">'
            f"<title>{esc(label)} — {fraction * 100:.1f}%</title></circle>"
        )
        offset += length
    parts.append(
        f'<text class="donut-total" x="{centre}" y="{centre - 2}">{_fmt(total)}</text>'
        f'<text class="donut-sub" x="{centre}" y="{centre + 16}">lines</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def legend(items: Sequence[tuple[str, float]], total: float) -> str:
    rows = []
    for index, (label, value) in enumerate(items):
        share = (value / total * 100) if total else 0
        rows.append(
            f'<li><i class="swatch c{index % CATEGORICAL}"></i>'
            f'<span class="l-name">{esc(label)}</span>'
            f'<span class="l-val">{_fmt(value)}</span>'
            f'<span class="l-pct">{share:.0f}%</span></li>'
        )
    return f'<ul class="legend-list">{"".join(rows)}</ul>'


# --------------------------------------------------------------------------
# Squarified treemap
# --------------------------------------------------------------------------

def _worst(row: list[float], length: float, scale: float) -> float:
    if not row or length <= 0:
        return math.inf
    total = sum(row) * scale
    side = total / length
    if side <= 0:
        return math.inf
    hi, lo = max(row) * scale, min(row) * scale
    return max((side * side) / (lo or 1e-9), hi / (side * side) if side else math.inf)


def _squarify(values: list[float], x: float, y: float, w: float, h: float) -> list[tuple[float, float, float, float]]:
    """Bruls/Huizing/van Wijk squarified treemap layout."""
    rects: list[tuple[float, float, float, float]] = []
    items = list(values)
    while items:
        if w <= 0 or h <= 0:
            rects.extend((x, y, 0.0, 0.0) for _ in items)
            break
        scale = (w * h) / sum(items) if sum(items) else 0
        length = min(w, h)
        row: list[float] = []
        while items:
            candidate = row + [items[0]]
            if row and _worst(candidate, length, scale) > _worst(row, length, scale):
                break
            row.append(items.pop(0))
        row_total = sum(row) * scale
        if length == w:  # lay the row out horizontally
            row_height = row_total / w if w else 0
            offset = x
            for value in row:
                width = (value * scale) / row_height if row_height else 0
                rects.append((offset, y, width, row_height))
                offset += width
            y += row_height
            h -= row_height
        else:            # lay the row out vertically
            row_width = row_total / h if h else 0
            offset = y
            for value in row:
                height = (value * scale) / row_width if row_width else 0
                rects.append((x, offset, row_width, height))
                offset += height
            x += row_width
            w -= row_width
    return rects


def treemap(items: Sequence[tuple[str, float, str]], *, width: int = 720, height: int = 320) -> str:
    """*items* is ``(label, value, tooltip)`` sorted descending by value."""
    data = [(label, float(value), tip) for label, value, tip in items if value > 0]
    if not data:
        return '<p class="empty">Nothing to chart.</p>'
    rects = _squarify([value for _, value, _ in data], 0, 0, width, height)
    parts = [
        f'<svg class="treemap" viewBox="0 0 {width} {height}" width="100%" '
        f'role="img" aria-label="Directory sizes">'
    ]
    for index, ((label, value, tip), (x, y, w, h)) in enumerate(zip(data, rects)):
        if w < 0.6 or h < 0.6:
            continue
        parts.append(
            f'<g class="tm c{index % CATEGORICAL}"><rect x="{x:.2f}" y="{y:.2f}" '
            f'width="{max(0.0, w - 2):.2f}" height="{max(0.0, h - 2):.2f}" rx="4">'
            f"<title>{esc(tip)}</title></rect>"
        )
        if w > 62 and h > 26:
            label_text = label if len(label) * 6.4 < w - 14 else label[: max(1, int((w - 20) / 6.4))] + "…"
            parts.append(
                f'<text class="tm-label" x="{x + 8:.2f}" y="{y + 18:.2f}">{esc(label_text)}</text>'
            )
            if h > 42:
                parts.append(
                    f'<text class="tm-sub" x="{x + 8:.2f}" y="{y + 33:.2f}">{_fmt(value)} lines</text>'
                )
        parts.append("</g>")
    parts.append("</svg>")
    return "".join(parts)


# --------------------------------------------------------------------------
# Hotspot scatter
# --------------------------------------------------------------------------

def scatter(points: Sequence[tuple[str, float, float, float, float]], *, width: int = 720, height: int = 360) -> str:
    """Revisions (x) against complexity (y), bubble area by size, colour by risk.

    *points* is ``(label, x, y, size, risk)``.
    """
    if not points:
        return '<p class="empty">Not enough history to plot.</p>'

    pad = {"l": 52, "r": 18, "t": 16, "b": 42}
    plot_w = width - pad["l"] - pad["r"]
    plot_h = height - pad["t"] - pad["b"]

    xs = [math.log1p(p[1]) for p in points]
    ys = [p[2] for p in points]
    sizes = [p[3] for p in points]
    x_max = max(xs) or 1.0
    y_max = max(ys) or 1.0
    size_max = max(sizes) or 1.0

    def px(value: float) -> float:
        return pad["l"] + (math.log1p(value) / x_max) * plot_w

    def py(value: float) -> float:
        return pad["t"] + plot_h - (value / y_max) * plot_h

    parts = [
        f'<svg class="scatter" viewBox="0 0 {width} {height}" width="100%" '
        f'role="img" aria-label="Churn against complexity">'
    ]
    for step in range(5):
        y = pad["t"] + plot_h * step / 4
        parts.append(f'<line class="grid" x1="{pad["l"]}" y1="{y:.1f}" x2="{width - pad["r"]}" y2="{y:.1f}"/>')
        parts.append(
            f'<text class="axis" x="{pad["l"] - 8}" y="{y + 4:.1f}" text-anchor="end">'
            f"{y_max * (1 - step / 4):.1f}</text>"
        )
    # A 200-file repo and a 5,000-file repo need different mark sizes: at the
    # top end, full-size bubbles merge into one unreadable blob. Shrink the
    # scale and thin the fill as the cloud gets denser.
    density = min(1.0, len(points) / 300)
    base, span = 3 + 1 * (1 - density), 6 + 10 * (1 - density)
    dense = " dense" if density > 0.6 else ""

    for point in sorted(points, key=lambda p: p[3], reverse=True):
        label, churn, complexity, size, risk = point
        radius = base + span * math.sqrt(size / size_max)
        heat = "hot" if risk >= 0.66 else ("warm" if risk >= 0.33 else "cool")
        parts.append(
            f'<circle class="pt {heat}{dense}" cx="{px(churn):.1f}" cy="{py(complexity):.1f}" '
            f'r="{radius:.1f}">'
            f"<title>{esc(label)}\n{int(churn)} revisions · complexity {complexity:.1f} · "
            f"{int(size)} lines</title></circle>"
        )
    parts.append(
        f'<text class="axis-title" x="{pad["l"] + plot_w / 2}" y="{height - 10}" text-anchor="middle">'
        "revisions (commits touching the file, log scale) →</text>"
    )
    parts.append(
        f'<text class="axis-title" x="14" y="{pad["t"] + plot_h / 2}" text-anchor="middle" '
        f'transform="rotate(-90 14 {pad["t"] + plot_h / 2})">↑ indentation complexity</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


# --------------------------------------------------------------------------
# Horizontal bars
# --------------------------------------------------------------------------

def bars(rows: Sequence[tuple[str, float, str]]) -> str:
    """*rows* is ``(label, value, right-hand caption)``."""
    if not rows:
        return '<p class="empty">No data.</p>'
    peak = max(value for _, value, _ in rows) or 1
    out = ['<ul class="bars">']
    for label, value, caption in rows:
        pct = max(1.5, value / peak * 100)
        out.append(
            f'<li><span class="b-label" title="{esc(label)}">{esc(label)}</span>'
            f'<span class="b-track"><span class="b-fill" style="width:{pct:.1f}%"></span></span>'
            f'<span class="b-value">{esc(caption)}</span></li>'
        )
    out.append("</ul>")
    return "".join(out)

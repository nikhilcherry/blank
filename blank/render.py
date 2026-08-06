"""Render a :class:`~blank.analyze.Report` into one self-contained HTML file.

The output has no external references at all — CSS and JS are inlined from the
files next to this module, and every chart is server-rendered SVG. Open it from
disk, mail it, drop it in a bucket; it works the same everywhere.
"""

from __future__ import annotations

import datetime as _dt
import json
from importlib import resources
from pathlib import Path

from . import charts
from .analyze import Report
from .charts import esc

_MAX_TABLE_ROWS = 250


def _asset(name: str) -> str:
    try:
        return resources.files(__package__).joinpath(name).read_text(encoding="utf-8")
    except (AttributeError, FileNotFoundError, ModuleNotFoundError):  # pragma: no cover
        return (Path(__file__).parent / name).read_text(encoding="utf-8")


def _num(value: float) -> str:
    return f"{value:,.0f}"


def _split_path(path: str) -> str:
    if "/" in path:
        head, _, tail = path.rpartition("/")
        return f'<span class="dir">{esc(head)}/</span>{esc(tail)}'
    return esc(path)


def _risk_class(score: float) -> str:
    return "hot" if score >= 0.5 else ("warm" if score >= 0.22 else "")


def _stat(label: str, value: str, note: str = "", flag: bool = False) -> str:
    cls = "stat flag" if flag else "stat"
    note_html = f'<div class="n">{esc(note)}</div>' if note else ""
    return f'<div class="{cls}"><div class="k">{esc(label)}</div><div class="v">{esc(value)}</div>{note_html}</div>'


def _hotspot_table(report: Report) -> str:
    rows = report.hotspots[:_MAX_TABLE_ROWS]
    if not rows:
        return '<p class="empty">No files scored — the repository may have too little history.</p>'
    now = report.last_commit or _dt.datetime.now(_dt.timezone.utc)
    body = []
    for record in rows:
        age = record.age_days(now)
        body.append(
            "<tr>"
            f'<td class="path">{_split_path(record.path)}</td>'
            f'<td class="num" data-v="{record.hotspot}">'
            f'<span class="risk {_risk_class(record.hotspot)}">{record.hotspot:.2f}</span></td>'
            f'<td class="num">{_num(record.commits)}</td>'
            f'<td class="num">{_num(record.churn)}</td>'
            f'<td class="num">{record.complexity:.1f}</td>'
            f'<td class="num">{_num(record.lines)}</td>'
            f'<td class="num">{record.author_count}</td>'
            f'<td class="num" data-v="{age if age is not None else -1}">{"—" if age is None else str(age) + "d"}</td>'
            "</tr>"
        )
    return f"""
<div class="table-wrap scroll-x">
  <table id="hotspot-table" data-sortable>
    <thead><tr>
      <th data-sort="text">File</th>
      <th data-sort="num" class="sorted">Risk</th>
      <th data-sort="num">Commits</th>
      <th data-sort="num">Churn</th>
      <th data-sort="num">Complexity</th>
      <th data-sort="num">Lines</th>
      <th data-sort="num">Authors</th>
      <th data-sort="num">Last touched</th>
    </tr></thead>
    <tbody>{"".join(body)}</tbody>
  </table>
</div>"""


def _authors_card(report: Report) -> str:
    top = report.authors[:12]
    if not top:
        return '<p class="empty">No authors found.</p>'
    rows = [
        (a.name, float(a.commits), f"{_num(a.commits)} commits · +{_num(a.added)}/-{_num(a.deleted)}")
        for a in top
    ]
    return charts.bars(rows)


def _coupling_card(report: Report) -> str:
    if not report.coupling:
        return (
            '<p class="empty">No strong coupling found — files change independently, '
            "which is usually good news.</p>"
        )
    items = []
    for a, b, shared, ratio in report.coupling[:12]:
        cross = a.split("/")[0] != b.split("/")[0]
        tag = '<span class="pill danger">cross-module</span>' if cross else '<span class="pill">same module</span>'
        items.append(
            f'<li><div class="p-files"><span>{_split_path(a)}</span>'
            f'<span class="arrow">⇄</span><span>{_split_path(b)}</span></div>'
            f'<div class="p-meta">changed together in {shared} commits · '
            f"{ratio * 100:.0f}% of the time {tag}</div></li>"
        )
    return f'<ul class="pairs">{"".join(items)}</ul>'


def _orphans_card(report: Report) -> str:
    if not report.orphans:
        return (
            '<p class="empty">Nothing orphaned. Every risky file still has an '
            "active author who has touched it recently.</p>"
        )
    items = []
    for record in report.orphans[:10]:
        items.append(
            f'<li><div class="p-files"><span>{_split_path(record.path)}</span></div>'
            f'<div class="p-meta">{record.ownership * 100:.0f}% written by '
            f"{esc(record.main_author)}, who has not committed in a while · "
            f"risk {record.hotspot:.2f}</div></li>"
        )
    return f'<ul class="pairs">{"".join(items)}</ul>'


def _language_card(report: Report) -> str:
    top = report.languages[:8]
    rest = report.languages[8:]
    slices = [(lang, float(lines)) for lang, _, lines in top]
    if rest:
        slices.append(("Other", float(sum(lines for _, _, lines in rest))))
    total = sum(value for _, value in slices)
    return (
        '<div class="donut-wrap">'
        + charts.donut(slices)
        + charts.legend(slices, total)
        + "</div>"
    )


def _treemap_card(report: Report) -> str:
    items = [
        (name if name != "." else "(root)", float(lines), f"{name} — {_num(lines)} lines in {files} files")
        for name, lines, files in report.directories[:26]
        if lines > 0
    ]
    return charts.treemap(items)


def _scatter_card(report: Report) -> str:
    points = [
        (f.path, float(f.commits), f.complexity, float(max(f.lines, 1)), f.hotspot)
        for f in report.code_files
        if f.commits > 0 and f.complexity > 0
    ]
    points.sort(key=lambda p: p[4], reverse=True)
    return charts.scatter(points[:400])


def render_html(report: Report) -> str:
    """Return the complete HTML document as a string."""
    now = report.last_commit or _dt.datetime.now(_dt.timezone.utc)
    active_90 = sum(1 for a in report.authors if a.active_days_ago(now) <= 90)
    hotspots = report.hotspots
    top_risk = hotspots[0] if hotspots else None
    span = (
        f"{report.first_commit:%b %Y} – {report.last_commit:%b %Y}"
        if report.first_commit and report.last_commit
        else "—"
    )

    stat_row = "".join(
        [
            _stat("Commits", _num(report.total_commits), span),
            _stat("Contributors", _num(len(report.authors)), f"{active_90} active in 90 days"),
            _stat("Tracked files", _num(len(report.files)), f"{_num(report.total_lines)} lines"),
            _stat(
                "Bus factor",
                str(report.bus_factor),
                "authors hold half the code",
                flag=report.bus_factor <= 2,
            ),
            _stat("Commit rate", f"{report.commits_per_week}", "per week, lifetime"),
            _stat(
                "Top risk",
                f"{top_risk.hotspot:.2f}" if top_risk else "—",
                top_risk.path.rsplit("/", 1)[-1] if top_risk else "no hotspots",
                flag=bool(top_risk and top_risk.hotspot >= 0.5),
            ),
        ]
    )

    notes = []
    if report.since:
        notes.append(
            f"History window: commits since <code>{esc(report.since)}</code>. "
            "Metrics describe this window only."
        )
    if report.thin_history:
        notes.append(
            f"<strong>Thin history.</strong> Only {report.scored_files} file"
            f"{'' if report.scored_files == 1 else 's'} have been revised more than once, "
            "so the risk ranking has almost nothing to work from. Complexity and composition "
            "are still accurate; treat the hotspot scores as unreliable until the repository "
            "has more history."
        )
    window_note = "".join(f'<div class="note">{note}</div>' for note in notes)

    remote = f' · <code>{esc(report.remote)}</code>' if report.remote else ""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(report.name)} — blank report</title>
<meta name="generator" content="blank {esc(report.version)}">
<style>{_asset("style.css")}</style>
</head>
<body>
<div class="wrap">

<header class="top">
  <div class="brand">
    <div class="mark">b</div>
    <div>
      <h1>{esc(report.name)}</h1>
      <div class="sub">branch <code>{esc(report.branch)}</code>{remote}<br>
        generated {report.generated:%Y-%m-%d %H:%M} by blank {esc(report.version)}</div>
    </div>
  </div>
  <div class="top-actions">
    <button class="ghost" id="theme-toggle" type="button">Theme</button>
  </div>
</header>

<div class="stats">{stat_row}</div>
{window_note}

<div class="grid" style="margin-top:18px">

  <section class="card span-12">
    <h2>Commit activity</h2>
    <p class="hint">Every day of the last year. Darker means more commits — gaps and
      crunch periods show up immediately.</p>
    <div class="scroll-x">{charts.heatmap(report.activity)}</div>
  </section>

  <section class="card span-7">
    <h2>Hotspots: revisions × complexity</h2>
    <p class="hint">Bottom-left is calm code. Top-right is code that is both tangled and
      constantly edited — that is where defects cluster. Bubble size is file length.</p>
    {_scatter_card(report)}
  </section>

  <section class="card span-5">
    <h2>Composition</h2>
    <p class="hint">Lines by language across tracked files.</p>
    {_language_card(report)}
  </section>

  <section class="card span-12">
    <div class="card-head">
      <div>
        <h2>Risk ranking</h2>
        <p class="hint">Sorted by risk score. Click any column to re-sort.
          Risk = normalised churn × normalised indentation complexity.</p>
      </div>
      <input class="filter" type="search" placeholder="Filter files…"
             data-filter="#hotspot-table" data-count="#hotspot-count" aria-label="Filter files">
    </div>
    {_hotspot_table(report)}
    <p class="hint" style="margin:10px 0 0" id="hotspot-count"></p>
  </section>

  <section class="card span-6">
    <h2>Temporal coupling</h2>
    <p class="hint">Files that keep changing in the same commit. Cross-module pairs
      usually mean a leaky abstraction or an unfinished refactor.</p>
    {_coupling_card(report)}
  </section>

  <section class="card span-6">
    <h2>Knowledge risk</h2>
    <p class="hint">Risky files written almost entirely by one person who has since gone
      quiet. These are the ones nobody left can safely change.</p>
    {_orphans_card(report)}
  </section>

  <section class="card span-6">
    <h2>Contributors</h2>
    <p class="hint">By commit count, all time in the analysed window.</p>
    {_authors_card(report)}
  </section>

  <section class="card span-6 fit">
    <h2>Where the code lives</h2>
    <p class="hint">Directory sizes by line count.</p>
    {_treemap_card(report)}
  </section>

</div>

<footer>
  <p>Generated by <a href="https://github.com/nikhilcherry/blank">blank</a> {esc(report.version)} —
  {_num(report.total_commits)} commits, {_num(len(report.files))} files analysed
  {"· " + _num(report.skipped_bulk) + " bulk commits excluded from ownership" if report.skipped_bulk else ""}.</p>
  <p>Complexity is an indentation proxy, not an AST metric. Bus factor counts authorship,
  not understanding. Read these as questions worth asking, not verdicts.</p>
</footer>

</div>
<script>{_asset("app.js")}</script>
</body>
</html>
"""


def render_markdown(report: Report, *, limit: int = 10) -> str:
    """A compact GitHub-flavoured summary.

    Sized for a PR comment or ``$GITHUB_STEP_SUMMARY`` — the tables stay short
    on purpose, because a 250-row risk ranking in a PR thread is noise.
    """
    now = report.last_commit or _dt.datetime.now(_dt.timezone.utc)
    lines: list[str] = [f"## `{report.name}` — code health", ""]

    bus = f"**{report.bus_factor}**" + (" ⚠️" if report.bus_factor <= 2 else "")
    lines.append(
        f"**{_num(report.total_commits)}** commits · **{_num(len(report.files))}** files · "
        f"**{_num(report.total_lines)}** lines · **{len(report.authors)}** contributors · "
        f"bus factor {bus}"
    )
    lines.append("")

    if report.since:
        lines.append(f"> History window: commits since `{report.since}`.")
        lines.append("")
    if report.thin_history:
        lines.append(
            f"> ⚠️ **Thin history** — only {report.scored_files} file(s) revised more than "
            "once, so the risk ranking below is not yet meaningful."
        )
        lines.append("")

    hotspots = report.hotspots[:limit]
    if hotspots:
        lines += [
            "### Top risks",
            "",
            "| File | Risk | Revisions | Complexity | Lines | Authors | Last touched |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for record in hotspots:
            age = record.age_days(now)
            mark = "🔴" if record.hotspot >= 0.5 else ("🟠" if record.hotspot >= 0.22 else "🟢")
            lines.append(
                f"| `{record.path}` | {mark} {record.hotspot:.2f} | {_num(record.commits)} | "
                f"{record.complexity:.1f} | {_num(record.lines)} | {record.author_count} | "
                f"{'—' if age is None else str(age) + 'd'} |"
            )
        lines.append("")

    if report.coupling:
        lines += ["### Files that change together", ""]
        for a, b, shared, ratio in report.coupling[:5]:
            cross = " — **cross-module**" if a.split("/")[0] != b.split("/")[0] else ""
            lines.append(f"- `{a}` ⇄ `{b}` — {ratio * 100:.0f}% of the time ({shared} commits){cross}")
        lines.append("")

    if report.orphans:
        lines += ["### Knowledge risk", ""]
        for record in report.orphans[:5]:
            lines.append(
                f"- `{record.path}` — {record.ownership * 100:.0f}% written by "
                f"{record.main_author}, who has not committed in a while"
            )
        lines.append("")

    total = report.total_lines or 1
    top_langs = " · ".join(
        f"{lang} {count / total * 100:.0f}%" for lang, _, count in report.languages[:4]
    )
    lines.append(f"<sub>{top_langs} — generated by blank {report.version}</sub>")
    return "\n".join(lines) + "\n"


def render_json(report: Report) -> str:
    """Machine-readable form of the same analysis, for CI checks and diffing."""
    now = report.last_commit or _dt.datetime.now(_dt.timezone.utc)
    payload = {
        "tool": "blank",
        "version": report.version,
        "repository": {
            "name": report.name,
            "branch": report.branch,
            "remote": report.remote,
            "root": report.root,
        },
        "generated": report.generated.isoformat(),
        "window": {
            "since": report.since,
            "commits": report.total_commits,
            "first_commit": report.first_commit.isoformat() if report.first_commit else None,
            "last_commit": report.last_commit.isoformat() if report.last_commit else None,
        },
        "summary": {
            "files": len(report.files),
            "lines": report.total_lines,
            "authors": len(report.authors),
            "bus_factor": report.bus_factor,
            "commits_per_week": report.commits_per_week,
        },
        "languages": [
            {"language": lang, "files": files, "lines": lines}
            for lang, files, lines in report.languages
        ],
        "hotspots": [
            {
                "path": f.path,
                "risk": f.hotspot,
                "commits": f.commits,
                "churn": f.churn,
                "complexity": f.complexity,
                "lines": f.lines,
                "authors": f.author_count,
                "main_author": f.main_author,
                "ownership": round(f.ownership, 3),
                "days_since_change": f.age_days(now),
            }
            for f in report.hotspots[:100]
        ],
        "coupling": [
            {"a": a, "b": b, "shared_commits": shared, "ratio": ratio}
            for a, b, shared, ratio in report.coupling
        ],
        "authors": [
            {
                "name": a.name,
                "email": a.email,
                "commits": a.commits,
                "added": a.added,
                "deleted": a.deleted,
                "files": len(a.files),
                "last_commit": a.last.isoformat() if a.last else None,
            }
            for a in report.authors
        ],
        "knowledge_risk": [
            {"path": f.path, "owner": f.main_author, "ownership": round(f.ownership, 3), "risk": f.hotspot}
            for f in report.orphans
        ],
    }
    return json.dumps(payload, indent=2)

"""Compare two JSON reports and describe what moved.

One report tells you where a codebase stands. Two, taken a quarter apart or on
either side of a pull request, tell you which direction it is heading — which is
the more actionable of the two questions.

Input is the JSON written by ``blank scan --json``; nothing here re-reads git,
so a baseline can be kept as a small artifact and compared against for months.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Risk is relative to the repository, so tiny movements are noise from files
# entering and leaving the normalisation set rather than real change.
NOISE_FLOOR = 0.05


class CompareError(ValueError):
    """Raised when an input is not a report blank can compare."""


@dataclass
class FileDelta:
    path: str
    before: float
    after: float

    @property
    def change(self) -> float:
        return round(self.after - self.before, 4)

    @property
    def kind(self) -> str:
        if self.before == 0.0:
            return "new"
        if self.after == 0.0:
            return "gone"
        return "worse" if self.change > 0 else "better"


@dataclass
class Comparison:
    name: str
    before_label: str
    after_label: str

    commits: tuple[int, int]
    files: tuple[int, int]
    lines: tuple[int, int]
    authors: tuple[int, int]
    bus_factor: tuple[int, int]

    worse: list[FileDelta] = field(default_factory=list)
    better: list[FileDelta] = field(default_factory=list)
    added: list[FileDelta] = field(default_factory=list)
    removed: list[FileDelta] = field(default_factory=list)
    new_coupling: list[tuple[str, str, float]] = field(default_factory=list)

    @property
    def regressed(self) -> list[FileDelta]:
        """Everything that got riskier, newly-appearing hotspots included."""
        return sorted(self.worse + self.added, key=lambda d: d.change, reverse=True)

    @property
    def worst_increase(self) -> float:
        deltas = [d.change for d in self.regressed]
        return max(deltas) if deltas else 0.0

    @property
    def unchanged(self) -> bool:
        return not (self.worse or self.better or self.added or self.removed)


def load(path: Path | str) -> dict[str, Any]:
    """Read one ``blank scan --json`` payload."""
    target = Path(path)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CompareError(f"{target} does not exist") from exc
    except json.JSONDecodeError as exc:
        raise CompareError(f"{target} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("tool") != "blank":
        raise CompareError(f"{target} is not a blank report (expected \"tool\": \"blank\")")
    return data


def _risks(payload: dict[str, Any]) -> dict[str, float]:
    return {
        entry["path"]: float(entry.get("risk", 0.0))
        for entry in payload.get("hotspots", [])
        if entry.get("path")
    }


def _pairs(payload: dict[str, Any]) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for entry in payload.get("coupling", []):
        a, b = entry.get("a"), entry.get("b")
        if a and b:
            out[tuple(sorted((a, b)))] = float(entry.get("ratio", 0.0))
    return out


def _label(payload: dict[str, Any]) -> str:
    window = payload.get("window", {})
    stamp = window.get("last_commit") or payload.get("generated") or "?"
    return str(stamp)[:10]


def compare(before: dict[str, Any], after: dict[str, Any], *, floor: float = NOISE_FLOOR) -> Comparison:
    """Diff two report payloads, oldest first."""
    old_risk, new_risk = _risks(before), _risks(after)

    result = Comparison(
        name=after.get("repository", {}).get("name", "?"),
        before_label=_label(before),
        after_label=_label(after),
        commits=(before["window"]["commits"], after["window"]["commits"]),
        files=(before["summary"]["files"], after["summary"]["files"]),
        lines=(before["summary"]["lines"], after["summary"]["lines"]),
        authors=(before["summary"]["authors"], after["summary"]["authors"]),
        bus_factor=(before["summary"]["bus_factor"], after["summary"]["bus_factor"]),
    )

    for path in sorted(set(old_risk) | set(new_risk)):
        delta = FileDelta(path, old_risk.get(path, 0.0), new_risk.get(path, 0.0))
        if abs(delta.change) < floor:
            continue
        bucket = {
            "new": result.added,
            "gone": result.removed,
            "worse": result.worse,
            "better": result.better,
        }[delta.kind]
        bucket.append(delta)

    result.worse.sort(key=lambda d: d.change, reverse=True)
    result.added.sort(key=lambda d: d.after, reverse=True)
    result.better.sort(key=lambda d: d.change)
    result.removed.sort(key=lambda d: d.before, reverse=True)

    old_pairs, new_pairs = _pairs(before), _pairs(after)
    result.new_coupling = sorted(
        ((a, b, ratio) for (a, b), ratio in new_pairs.items() if (a, b) not in old_pairs),
        key=lambda row: row[2],
        reverse=True,
    )[:10]

    return result


def render_markdown(diff: Comparison, *, limit: int = 10) -> str:
    """A Markdown summary of the comparison, for PR comments."""
    lines = [f"## `{diff.name}` — what moved", ""]
    lines.append(f"Comparing **{diff.before_label}** → **{diff.after_label}**")
    lines.append("")
    lines += [
        "| | Before | After | Change |",
        "| --- | ---: | ---: | ---: |",
    ]
    for label, (old, new) in (
        ("Commits", diff.commits),
        ("Files", diff.files),
        ("Lines", diff.lines),
        ("Contributors", diff.authors),
        ("Bus factor", diff.bus_factor),
    ):
        change = new - old
        arrow = "—" if change == 0 else (f"+{change:,}" if change > 0 else f"{change:,}")
        lines.append(f"| {label} | {old:,} | {new:,} | {arrow} |")
    lines.append("")

    if diff.unchanged:
        lines.append("No file moved more than the noise floor. Nothing to report.")
        return "\n".join(lines) + "\n"

    for title, rows, sign in (
        ("🔺 Got riskier", diff.worse[:limit], "+"),
        ("🆕 New hotspots", diff.added[:limit], ""),
        ("🔻 Improved", diff.better[:limit], ""),
        ("✅ No longer ranked", diff.removed[:limit], ""),
    ):
        if not rows:
            continue
        lines += [f"### {title}", ""]
        for delta in rows:
            if delta.kind == "new":
                lines.append(f"- `{delta.path}` — now **{delta.after:.2f}**")
            elif delta.kind == "gone":
                lines.append(f"- `{delta.path}` — was {delta.before:.2f}")
            else:
                lines.append(
                    f"- `{delta.path}` — {delta.before:.2f} → **{delta.after:.2f}** "
                    f"({sign}{delta.change:.2f})"
                )
        lines.append("")

    if diff.new_coupling:
        lines += ["### 🔗 Newly coupled", ""]
        for a, b, ratio in diff.new_coupling[:5]:
            lines.append(f"- `{a}` ⇄ `{b}` — {ratio * 100:.0f}% of the time")
        lines.append("")

    lines.append("<sub>Generated by blank</sub>")
    return "\n".join(lines) + "\n"

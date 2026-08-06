"""Turn raw history + a file scan into the numbers the report is made of.

Metric definitions live here and nowhere else, so there is exactly one place to
argue with. Each one is documented with what it means and what it does *not*.
"""

from __future__ import annotations

import datetime as _dt
import math
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__, gitlog, scan
from .gitlog import Commit
from .scan import FileInfo

# A commit touching more than this many files is a bulk move, a vendored drop
# or a formatting sweep. It still counts for activity, but it is excluded from
# coupling and ownership so it cannot invent relationships between files.
BULK_COMMIT_FILES = 40

# Coupling needs enough evidence to be worth printing.
MIN_COUPLING_COMMITS = 4
MIN_COUPLING_RATIO = 0.35

STALE_DAYS = 365


@dataclass
class FileMetrics:
    """Everything known about one file, history and content joined."""

    path: str
    language: str
    lines: int
    code_lines: int
    complexity: float
    commits: int = 0
    added: int = 0
    deleted: int = 0
    authors: Counter = field(default_factory=Counter)
    first_seen: _dt.datetime | None = None
    last_seen: _dt.datetime | None = None
    hotspot: float = 0.0

    @property
    def churn(self) -> int:
        return self.added + self.deleted

    @property
    def author_count(self) -> int:
        return len(self.authors)

    @property
    def main_author(self) -> str:
        return self.authors.most_common(1)[0][0] if self.authors else "—"

    @property
    def ownership(self) -> float:
        """Share of authored lines held by the top contributor (0..1)."""
        total = sum(self.authors.values())
        if not total:
            return 0.0
        return self.authors.most_common(1)[0][1] / total

    def age_days(self, now: _dt.datetime) -> int | None:
        if self.last_seen is None:
            return None
        return max(0, (now - self.last_seen).days)


@dataclass
class AuthorStats:
    name: str
    email: str
    commits: int = 0
    added: int = 0
    deleted: int = 0
    files: set[str] = field(default_factory=set)
    first: _dt.datetime | None = None
    last: _dt.datetime | None = None

    @property
    def net(self) -> int:
        return self.added - self.deleted

    def active_days_ago(self, now: _dt.datetime) -> int:
        return max(0, (now - self.last).days) if self.last else 10**6


@dataclass
class Report:
    """The complete analysis. Renderers consume this and nothing else."""

    name: str
    root: str
    branch: str
    remote: str | None
    generated: _dt.datetime
    version: str

    files: list[FileMetrics]
    authors: list[AuthorStats]
    languages: list[tuple[str, int, int]]          # (language, files, lines)
    activity: list[tuple[_dt.date, int]]           # daily commit counts
    coupling: list[tuple[str, str, int, float]]    # (a, b, shared commits, ratio)
    directories: list[tuple[str, int, int]]        # (dir, lines, files)
    orphans: list[FileMetrics]
    stale: list[FileMetrics]

    total_commits: int
    window_commits: int
    first_commit: _dt.datetime | None
    last_commit: _dt.datetime | None
    bus_factor: int
    since: str | None
    skipped_bulk: int

    @property
    def total_lines(self) -> int:
        return sum(f.lines for f in self.files)

    @property
    def code_files(self) -> list[FileMetrics]:
        return [f for f in self.files if f.language not in scan.NON_CODE]

    @property
    def hotspots(self) -> list[FileMetrics]:
        return sorted(
            (f for f in self.code_files if f.hotspot > 0),
            key=lambda f: f.hotspot,
            reverse=True,
        )

    @property
    def age_days(self) -> int:
        if not (self.first_commit and self.last_commit):
            return 0
        return max(1, (self.last_commit - self.first_commit).days)

    @property
    def commits_per_week(self) -> float:
        weeks = max(1.0, self.age_days / 7)
        return round(self.total_commits / weeks, 1)


def _norm(values: Sequence[float]) -> list[float]:
    """Scale to 0..1 with a log first — churn is heavy-tailed."""
    if not values:
        return []
    logged = [math.log1p(max(0.0, v)) for v in values]
    lo, hi = min(logged), max(logged)
    if hi - lo < 1e-9:
        return [0.0 for _ in logged]
    return [(v - lo) / (hi - lo) for v in logged]


def _bus_factor(authors: Iterable[AuthorStats], threshold: float = 0.5) -> int:
    """Fewest authors whose contributions cover *threshold* of all lines added.

    The classic reading: lose this many people and half the institutional
    knowledge walks out. It measures authorship, not who *understands* the
    code — a reviewer-heavy team is more resilient than this number suggests.
    """
    weights = sorted((a.added for a in authors), reverse=True)
    total = sum(weights)
    if total <= 0:
        return 0
    running = 0
    for count, weight in enumerate(weights, start=1):
        running += weight
        if running >= total * threshold:
            return count
    return len(weights)


def _tracked_files(repo: Path) -> set[str]:
    out = gitlog.run_git(repo, ["ls-files"], check=False)
    return {line for line in out.splitlines() if line.strip()}


def _daily_activity(commits: Sequence[Commit], days: int = 371) -> list[tuple[_dt.date, int]]:
    if not commits:
        return []
    end = max(c.when for c in commits).date()
    start = end - _dt.timedelta(days=days - 1)
    counts: Counter[_dt.date] = Counter()
    for commit in commits:
        day = commit.when.date()
        if start <= day <= end:
            counts[day] += 1
    span = (end - start).days + 1
    return [(start + _dt.timedelta(days=i), counts.get(start + _dt.timedelta(days=i), 0)) for i in range(span)]


def _coupling(commits: Sequence[Commit], canonical: dict[str, str], keep: set[str]) -> list[tuple[str, str, int, float]]:
    """Temporal coupling: files that keep changing in the same commit.

    High coupling between files in different modules is the interesting case —
    it means an abstraction is leaking, or a rename never finished.
    """
    pair_counts: Counter[tuple[str, str]] = Counter()
    file_counts: Counter[str] = Counter()
    for commit in commits:
        paths = sorted({canonical.get(f.path, f.path) for f in commit.files})
        paths = [p for p in paths if p in keep]
        if not 2 <= len(paths) <= BULK_COMMIT_FILES:
            for path in paths:
                file_counts[path] += 1
            continue
        for path in paths:
            file_counts[path] += 1
        for i, a in enumerate(paths):
            for b in paths[i + 1:]:
                pair_counts[(a, b)] += 1

    results: list[tuple[str, str, int, float]] = []
    for (a, b), shared in pair_counts.items():
        if shared < MIN_COUPLING_COMMITS:
            continue
        base = min(file_counts[a], file_counts[b])
        if base <= 0:
            continue
        ratio = shared / base
        if ratio >= MIN_COUPLING_RATIO:
            results.append((a, b, shared, round(ratio, 3)))
    results.sort(key=lambda row: (row[3], row[2]), reverse=True)
    return results[:40]


def build_report(
    path: Path,
    *,
    since: str | None = None,
    max_commits: int | None = None,
    include_merges: bool = False,
) -> Report:
    """Analyse the repository containing *path* and return a :class:`Report`."""
    root = gitlog.repo_root(path)
    commits = gitlog.read_history(root, since=since, max_commits=max_commits, include_merges=include_merges)
    canonical = gitlog.follow_renames(commits)

    tracked = _tracked_files(root)
    disk = scan.scan_tree(root, tracked or None)
    by_path: dict[str, FileInfo] = {f.path: f for f in disk}

    metrics: dict[str, FileMetrics] = {
        info.path: FileMetrics(
            path=info.path,
            language=info.language,
            lines=info.lines,
            code_lines=info.code_lines,
            complexity=info.complexity,
        )
        for info in disk
    }

    authors: dict[str, AuthorStats] = {}
    skipped_bulk = 0

    for commit in commits:
        key = commit.email or commit.author.lower()
        stats = authors.get(key)
        if stats is None:
            stats = authors[key] = AuthorStats(name=commit.author, email=commit.email)
        stats.commits += 1
        stats.first = commit.when if stats.first is None else min(stats.first, commit.when)
        stats.last = commit.when if stats.last is None else max(stats.last, commit.when)

        bulk = len(commit.files) > BULK_COMMIT_FILES
        if bulk:
            skipped_bulk += 1

        for change in commit.files:
            current = canonical.get(change.path, change.path)
            stats.added += change.added
            stats.deleted += change.deleted
            record = metrics.get(current)
            if record is None:
                continue  # deleted since, or filtered out of the scan
            stats.files.add(current)
            record.commits += 1
            record.added += change.added
            record.deleted += change.deleted
            record.first_seen = commit.when if record.first_seen is None else min(record.first_seen, commit.when)
            record.last_seen = commit.when if record.last_seen is None else max(record.last_seen, commit.when)
            if not bulk:
                record.authors[commit.author] += max(1, change.added)

    files = list(metrics.values())

    # Hotspot = churn x complexity, both log-normalised to 0..1. A file that is
    # complex but never touched is fine; a file touched daily but flat is fine.
    # The product is what hurts.
    code = [f for f in files if f.language not in scan.NON_CODE]
    churn_scores = _norm([f.churn for f in code])
    cx_scores = _norm([f.complexity for f in code])
    for record, churn_score, cx_score in zip(code, churn_scores, cx_scores):
        record.hotspot = round(churn_score * cx_score, 4)

    lang_files: Counter[str] = Counter()
    lang_lines: Counter[str] = Counter()
    for info in disk:
        lang_files[info.language] += 1
        lang_lines[info.language] += info.lines
    languages = sorted(
        ((lang, lang_files[lang], lang_lines[lang]) for lang in lang_files),
        key=lambda row: row[2],
        reverse=True,
    )

    dir_lines: Counter[str] = Counter()
    dir_files: Counter[str] = Counter()
    for info in disk:
        parent = info.path.rsplit("/", 1)[0] if "/" in info.path else "."
        dir_lines[parent] += info.lines
        dir_files[parent] += 1
    directories = sorted(
        ((name, dir_lines[name], dir_files[name]) for name in dir_lines),
        key=lambda row: row[1],
        reverse=True,
    )

    now = max((c.when for c in commits), default=_dt.datetime.now(_dt.timezone.utc))
    author_list = sorted(authors.values(), key=lambda a: a.commits, reverse=True)

    # Orphaned hotspots: risky code whose only real author has gone quiet.
    orphans = [
        f
        for f in sorted(code, key=lambda f: f.hotspot, reverse=True)
        if f.hotspot > 0.15
        and f.ownership >= 0.8
        and _author_by_name(author_list, f.main_author, now) > 180
    ][:15]

    stale = sorted(
        (f for f in code if f.last_seen and (now - f.last_seen).days >= STALE_DAYS and f.lines > 30),
        key=lambda f: f.last_seen or now,
    )[:15]

    return Report(
        name=root.name or str(root),
        root=str(root),
        branch=gitlog.current_branch(root),
        remote=gitlog.remote_url(root),
        generated=_dt.datetime.now().astimezone(),
        version=__version__,
        files=files,
        authors=author_list,
        languages=languages,
        activity=_daily_activity(commits),
        coupling=_coupling(commits, canonical, set(by_path)),
        directories=directories,
        orphans=orphans,
        stale=stale,
        total_commits=len(commits),
        window_commits=len(commits),
        first_commit=min((c.when for c in commits), default=None),
        last_commit=max((c.when for c in commits), default=None),
        bus_factor=_bus_factor(author_list),
        since=since,
        skipped_bulk=skipped_bulk,
    )


def _author_by_name(authors: Sequence[AuthorStats], name: str, now: _dt.datetime) -> int:
    for author in authors:
        if author.name == name:
            return author.active_days_ago(now)
    return 10**6

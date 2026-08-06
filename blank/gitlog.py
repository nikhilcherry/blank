"""Read history out of a Git repository.

Everything here shells out to ``git`` and parses the plumbing-ish output of
``git log --numstat``. No third-party dependencies, no libgit2, no network.

The format we ask for is:

    \\x01<sha>\\x1f<author name>\\x1f<author email>\\x1f<iso date>\\x1f<subject>
    <added>\\t<deleted>\\t<path>
    <added>\\t<deleted>\\t<path>
    ...

``\\x01`` starts a commit record and ``\\x1f`` separates its fields, so commit
subjects containing tabs, newlines or pipes cannot corrupt the parse.
"""

from __future__ import annotations

import datetime as _dt
import re
import subprocess
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

REC = "\x01"
SEP = "\x1f"
_PRETTY = f"{REC}%H{SEP}%an{SEP}%aE{SEP}%aI{SEP}%s"

# "old/{a => b}/file.py" and "old.py => new.py"
_BRACE_RENAME = re.compile(r"^(.*)\{(.*) => (.*)\}(.*)$")


class GitError(RuntimeError):
    """Raised when git is missing, or the path is not a usable repository."""


@dataclass(frozen=True)
class FileChange:
    """One file touched by one commit."""

    path: str
    added: int
    deleted: int
    binary: bool = False
    old_path: str | None = None

    @property
    def churn(self) -> int:
        return self.added + self.deleted


@dataclass
class Commit:
    sha: str
    author: str
    email: str
    when: _dt.datetime
    subject: str
    files: list[FileChange] = field(default_factory=list)

    @property
    def is_large(self) -> bool:
        """Bulk imports and vendored drops skew every metric; flag them."""
        return len(self.files) > 100


def run_git(repo: Path, args: Sequence[str], *, check: bool = True) -> str:
    """Run ``git <args>`` inside *repo* and return stdout as text."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            errors="replace",
        )
    except FileNotFoundError as exc:  # pragma: no cover - environment dependent
        raise GitError("`git` was not found on PATH") from exc
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        hint = detail[0] if detail else f"exit status {proc.returncode}"
        raise GitError(f"git {' '.join(args)} failed: {hint}")
    return proc.stdout


def repo_root(path: Path) -> Path:
    """Resolve *path* to the root of the repository that contains it."""
    if not path.exists():
        raise GitError(f"{path} does not exist")
    out = run_git(path if path.is_dir() else path.parent, ["rev-parse", "--show-toplevel"])
    root = out.strip()
    if not root:
        raise GitError(f"{path} is not inside a Git repository")
    return Path(root)


def has_commits(repo: Path) -> bool:
    out = run_git(repo, ["rev-parse", "--verify", "--quiet", "HEAD"], check=False)
    return bool(out.strip())


def current_branch(repo: Path) -> str:
    return run_git(repo, ["rev-parse", "--abbrev-ref", "HEAD"], check=False).strip() or "HEAD"


def remote_url(repo: Path) -> str | None:
    url = run_git(repo, ["config", "--get", "remote.origin.url"], check=False).strip()
    return url or None


def _split_rename(path: str) -> tuple[str, str | None]:
    """Return ``(new_path, old_path)`` for a numstat path field."""
    m = _BRACE_RENAME.match(path)
    if m:
        prefix, old, new, suffix = m.groups()
        norm = lambda part: re.sub(r"//+", "/", f"{prefix}{part}{suffix}")  # noqa: E731
        return norm(new), norm(old)
    if " => " in path:
        old, _, new = path.partition(" => ")
        return new.strip(), old.strip()
    return path, None


def _parse_numstat(line: str) -> FileChange | None:
    parts = line.split("\t")
    if len(parts) < 3:
        return None
    raw_added, raw_deleted, raw_path = parts[0], parts[1], "\t".join(parts[2:])
    binary = raw_added == "-" or raw_deleted == "-"
    added = 0 if binary else int(raw_added or 0)
    deleted = 0 if binary else int(raw_deleted or 0)
    new_path, old_path = _split_rename(raw_path)
    return FileChange(new_path, added, deleted, binary=binary, old_path=old_path)


def parse_log(text: str) -> Iterator[Commit]:
    """Parse the output of :func:`log_command`'s format into commits."""
    for chunk in text.split(REC):
        if not chunk.strip():
            continue
        header, _, body = chunk.partition("\n")
        fields = header.split(SEP)
        if len(fields) < 5:
            continue
        sha, author, email, when, subject = fields[:5]
        try:
            stamp = _dt.datetime.fromisoformat(when)
        except ValueError:
            continue
        commit = Commit(
            sha=sha.strip(),
            author=author.strip() or "(unknown)",
            email=email.strip().lower(),
            when=stamp,
            subject=subject.strip(),
        )
        for line in body.splitlines():
            if not line.strip():
                continue
            change = _parse_numstat(line)
            if change is not None:
                commit.files.append(change)
        yield commit


def log_command(*, since: str | None, max_commits: int | None, include_merges: bool) -> list[str]:
    args = ["log", f"--pretty=format:{_PRETTY}", "--numstat", "-M", "--date=iso-strict"]
    if not include_merges:
        args.append("--no-merges")
    if since:
        args.append(f"--since={since}")
    if max_commits:
        args.append(f"-n{max_commits}")
    return args


def read_history(
    repo: Path,
    *,
    since: str | None = None,
    max_commits: int | None = None,
    include_merges: bool = False,
) -> list[Commit]:
    """Load commits, newest first."""
    if not has_commits(repo):
        return []
    text = run_git(repo, log_command(since=since, max_commits=max_commits, include_merges=include_merges))
    return list(parse_log(text))


def follow_renames(commits: Iterable[Commit]) -> dict[str, str]:
    """Map historical paths to the name a file goes by today.

    ``git log`` walks newest-to-oldest, so the first name we see for a file is
    its current one. Later (older) renames chain back onto it.
    """
    canonical: dict[str, str] = {}
    for commit in commits:
        for change in commit.files:
            current = canonical.get(change.path, change.path)
            if change.old_path:
                canonical[change.old_path] = current
            canonical.setdefault(change.path, current)
    # Collapse chains: a -> b -> c should resolve straight to c.
    resolved: dict[str, str] = {}
    for start in canonical:
        seen = {start}
        node = start
        while True:
            nxt = canonical.get(node, node)
            if nxt == node or nxt in seen:
                break
            seen.add(nxt)
            node = nxt
        resolved[start] = node
    return resolved

"""Walk the working tree: languages, line counts, indentation complexity."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Extension -> language. Deliberately opinionated and short; unknown extensions
# fall through to "Other" rather than pretending to be precise.
LANGUAGES: dict[str, str] = {
    ".py": "Python", ".pyi": "Python",
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP", ".java": "Java",
    ".kt": "Kotlin", ".kts": "Kotlin", ".swift": "Swift", ".scala": "Scala",
    ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++", ".cxx": "C++", ".hpp": "C++", ".hh": "C++",
    ".cs": "C#", ".m": "Objective-C", ".mm": "Objective-C", ".ex": "Elixir", ".exs": "Elixir",
    ".erl": "Erlang", ".hs": "Haskell", ".clj": "Clojure", ".lua": "Lua", ".dart": "Dart",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell", ".fish": "Shell", ".ps1": "PowerShell",
    ".sql": "SQL", ".r": "R", ".jl": "Julia", ".pl": "Perl", ".vim": "Vim script",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "CSS", ".sass": "CSS", ".less": "CSS",
    ".vue": "Vue", ".svelte": "Svelte", ".astro": "Astro",
    ".md": "Markdown", ".mdx": "Markdown", ".rst": "reStructuredText", ".txt": "Text",
    ".json": "JSON", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML", ".ini": "Config",
    ".cfg": "Config", ".xml": "XML", ".proto": "Protobuf", ".graphql": "GraphQL",
    ".tf": "Terraform", ".dockerfile": "Docker", ".nix": "Nix", ".zig": "Zig",
}

FILENAME_LANGUAGES: dict[str, str] = {
    "dockerfile": "Docker",
    "makefile": "Make",
    "justfile": "Just",
    "rakefile": "Ruby",
    "gemfile": "Ruby",
    "cmakelists.txt": "CMake",
}

# Languages that are content, not engineering surface. Reported, but excluded
# from hotspot ranking so a churning CHANGELOG never tops the risk list.
NON_CODE = {
    "Markdown", "Text", "JSON", "YAML", "TOML", "Config", "XML",
    "reStructuredText", "CSV", "Other",
}

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "vendor", "venv", ".venv", "env",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    "dist", "build", "target", "out", ".next", ".nuxt", ".svelte-kit",
    "coverage", ".idea", ".vscode", ".gradle", "Pods", ".terraform", ".cache",
}

SKIP_SUFFIXES = {
    ".lock", ".min.js", ".min.css", ".map", ".png", ".jpg", ".jpeg", ".gif",
    ".webp", ".svg", ".ico", ".pdf", ".zip", ".gz", ".tar", ".bz2", ".xz",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3", ".mp4", ".mov", ".wav",
    ".so", ".dylib", ".dll", ".exe", ".class", ".jar", ".pyc", ".o", ".a",
    ".bin", ".dat", ".db", ".sqlite", ".parquet",
}

MAX_BYTES = 2_000_000  # anything larger is almost certainly generated
TAB_WIDTH = 4


@dataclass
class FileInfo:
    """A single tracked file as it exists on disk right now."""

    path: str
    language: str
    bytes: int
    lines: int
    code_lines: int
    mean_indent: float
    max_indent: int
    binary: bool = False

    @property
    def is_code(self) -> bool:
        return not self.binary and self.language not in NON_CODE

    @property
    def complexity(self) -> float:
        """Indentation complexity: a language-agnostic proxy for nesting.

        Deeply indented code is hard to reason about regardless of syntax, and
        unlike a real AST metric this works on every language at once. It is a
        proxy, not a measurement — treat it as "worth a look", not "broken".
        """
        if self.code_lines < 5:
            return 0.0
        return round(self.mean_indent + 0.35 * self.max_indent, 3)


def classify(path: str) -> str:
    name = os.path.basename(path).lower()
    if name in FILENAME_LANGUAGES:
        return FILENAME_LANGUAGES[name]
    if name.startswith("dockerfile"):
        return "Docker"
    _, ext = os.path.splitext(name)
    return LANGUAGES.get(ext, "Other")


def should_skip(rel_path: str) -> bool:
    parts = rel_path.split("/")
    if any(part in SKIP_DIRS for part in parts[:-1]):
        return True
    name = parts[-1].lower()
    return any(name.endswith(suffix) for suffix in SKIP_SUFFIXES)


def _indent_units(line: str) -> int:
    units = 0
    for ch in line:
        if ch == " ":
            units += 1
        elif ch == "\t":
            units += TAB_WIDTH
        else:
            break
    return units // TAB_WIDTH


def measure(path: Path) -> tuple[int, int, float, int, bool]:
    """Return ``(lines, code_lines, mean_indent, max_indent, binary)``."""
    try:
        raw = path.read_bytes()
    except OSError:
        return 0, 0, 0.0, 0, True
    if b"\0" in raw[:8192]:
        return 0, 0, 0.0, 0, True
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    indents: list[int] = []
    for line in lines:
        if line.strip():
            indents.append(_indent_units(line))
    code_lines = len(indents)
    mean = round(sum(indents) / code_lines, 3) if code_lines else 0.0
    return len(lines), code_lines, mean, (max(indents) if indents else 0), False


def scan_tree(root: Path, tracked: set[str] | None = None) -> list[FileInfo]:
    """Measure every interesting file under *root*.

    If *tracked* is given (paths relative to root, as Git reports them), only
    those files are considered — that keeps untracked scratch files out.
    """
    results: list[FileInfo] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".git"))
        for filename in sorted(filenames):
            full = Path(dirpath) / filename
            rel = full.relative_to(root).as_posix()
            if tracked is not None and rel not in tracked:
                continue
            if should_skip(rel):
                continue
            try:
                size = full.stat().st_size
            except OSError:
                continue
            if size > MAX_BYTES:
                continue
            lines, code_lines, mean_indent, max_indent, binary = measure(full)
            if binary:
                continue
            results.append(
                FileInfo(
                    path=rel,
                    language=classify(rel),
                    bytes=size,
                    lines=lines,
                    code_lines=code_lines,
                    mean_indent=mean_indent,
                    max_indent=max_indent,
                )
            )
    return results

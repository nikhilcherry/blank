"""Command line interface for blank."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from . import __version__
from .analyze import Report, build_report
from .gitlog import GitError
from .render import render_html, render_json, render_markdown
from .term import make_style, render_authors, render_coupling, render_hotspots, render_summary

EPILOG = """\
examples:
  blank stats                        summary of the repo you are standing in
  blank scan ~/code/api -o api.html  write a shareable HTML report
  blank scan . --open                write blank-report.html and open it
  blank hotspots -n 30 --since=1.year
  blank check --min-bus-factor 2 --max-risk 0.85   gate a CI build
  blank scan . --json - | jq .summary
  blank scan . --markdown "$GITHUB_STEP_SUMMARY"
"""


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", nargs="?", default=".", help="repository path (default: .)")
    parser.add_argument(
        "--since",
        metavar="DATE",
        help="only analyse commits after DATE — anything git understands, "
             "e.g. 2024-01-01, '18 months ago', 1.year",
    )
    parser.add_argument(
        "--max-commits", type=int, metavar="N", help="stop after N commits (newest first)"
    )
    parser.add_argument(
        "--include-merges", action="store_true", help="count merge commits (off by default)"
    )
    colour = parser.add_mutually_exclusive_group()
    colour.add_argument("--color", dest="color", action="store_true", default=None, help="force colour")
    colour.add_argument("--no-color", dest="color", action="store_false", help="disable colour")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blank",
        description="Turn a Git repository into an insight report: hotspots, coupling, "
                    "ownership and activity. No dependencies, no network, no telemetry.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-V", "--version", action="version", version=f"blank {__version__}")
    subs = parser.add_subparsers(dest="command")

    scan = subs.add_parser("scan", help="write a self-contained HTML report")
    _add_common(scan)
    scan.add_argument(
        "-o", "--output", default="blank-report.html", metavar="FILE",
        help="output file, or - for stdout (default: blank-report.html)",
    )
    scan.add_argument("--json", metavar="FILE", help="also write the raw analysis as JSON ('-' for stdout)")
    scan.add_argument(
        "--markdown", metavar="FILE",
        help="also write a compact Markdown summary, sized for a PR comment or "
             "$GITHUB_STEP_SUMMARY ('-' for stdout)",
    )
    scan.add_argument("--open", dest="open_browser", action="store_true", help="open the report when done")

    stats = subs.add_parser("stats", help="print a summary in the terminal")
    _add_common(stats)

    hotspots = subs.add_parser("hotspots", help="rank files by churn × complexity")
    _add_common(hotspots)
    hotspots.add_argument("-n", "--limit", type=int, default=20, help="rows to show (default: 20)")

    authors = subs.add_parser("authors", help="contribution breakdown")
    _add_common(authors)
    authors.add_argument("-n", "--limit", type=int, default=20, help="rows to show (default: 20)")

    coupling = subs.add_parser("coupling", help="files that change together")
    _add_common(coupling)
    coupling.add_argument("-n", "--limit", type=int, default=20, help="rows to show (default: 20)")

    check = subs.add_parser("check", help="fail a build when thresholds are crossed")
    _add_common(check)
    check.add_argument("--max-risk", type=float, metavar="R", help="fail if any file scores above R (0..1)")
    check.add_argument("--min-bus-factor", type=int, metavar="N", help="fail if the bus factor is below N")
    check.add_argument("--max-orphans", type=int, metavar="N", help="fail if more than N orphaned hotspots exist")

    return parser


def _load(args: argparse.Namespace) -> Report:
    return build_report(
        Path(args.path).expanduser(),
        since=args.since,
        max_commits=args.max_commits,
        include_merges=args.include_merges,
    )


def _write(path: str, text: str, label: str, style) -> None:
    if path == "-":
        sys.stdout.write(text)
        return
    target = Path(path).expanduser()
    if target.parent != Path(""):
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    size = target.stat().st_size / 1024
    print(f"{style.green('✓')} {label} → {style.bold(str(target))} {style.dim(f'({size:.0f} KB)')}")


def _cmd_scan(args: argparse.Namespace, report: Report, style) -> int:
    _write(args.output, render_html(report), "report", style)
    if args.json:
        _write(args.json, render_json(report), "json", style)
    if args.markdown:
        _write(args.markdown, render_markdown(report), "markdown", style)
    if args.open_browser and args.output != "-":
        webbrowser.open(Path(args.output).expanduser().resolve().as_uri())
    return 0


def _cmd_check(args: argparse.Namespace, report: Report, style) -> int:
    failures: list[str] = []
    if args.max_risk is not None:
        over = [f for f in report.hotspots if f.hotspot > args.max_risk]
        for record in over[:10]:
            failures.append(f"risk {record.hotspot:.2f} > {args.max_risk} — {record.path}")
        if len(over) > 10:
            failures.append(f"…and {len(over) - 10} more files over the risk threshold")
    if args.min_bus_factor is not None and report.bus_factor < args.min_bus_factor:
        failures.append(f"bus factor {report.bus_factor} < {args.min_bus_factor}")
    if args.max_orphans is not None and len(report.orphans) > args.max_orphans:
        failures.append(f"{len(report.orphans)} orphaned hotspots > {args.max_orphans}")

    if not any(v is not None for v in (args.max_risk, args.min_bus_factor, args.max_orphans)):
        print(style.yellow("no thresholds given; nothing to check"), file=sys.stderr)
        return 2

    if failures:
        print(style.red(f"✗ blank check failed ({len(failures)} findings)"))
        for line in failures:
            print(f"  {style.red('·')} {line}")
        return 1
    print(style.green("✓ blank check passed"))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    style = make_style(args.color)
    try:
        report = _load(args)
    except GitError as exc:
        print(f"{style.red('error:')} {exc}", file=sys.stderr)
        return 2

    if report.total_commits == 0 and args.command != "scan":
        print(style.yellow("no commits found in this repository (or in the selected window)"))
        return 0

    if args.command == "scan":
        return _cmd_scan(args, report, style)
    if args.command == "stats":
        print(render_summary(report, style))
        return 0
    if args.command == "hotspots":
        print(render_hotspots(report, style, args.limit))
        return 0
    if args.command == "authors":
        print(render_authors(report, style, args.limit))
        return 0
    if args.command == "coupling":
        print(render_coupling(report, style, args.limit))
        return 0
    if args.command == "check":
        return _cmd_check(args, report, style)

    parser.print_help()
    return 0


def entry() -> None:  # console_scripts target
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:  # pragma: no cover
        raise SystemExit(130) from None


if __name__ == "__main__":  # pragma: no cover
    entry()

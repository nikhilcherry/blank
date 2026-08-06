<div align="center">

# blank

**Point it at a Git repository. Get back one HTML file that explains the codebase.**

Hotspots, temporal coupling, knowledge risk, activity — computed from history and content,
rendered into a single self-contained page you can email, commit, or drop in a bucket.

[![CI](https://github.com/nikhilcherry/blank/actions/workflows/ci.yml/badge.svg)](https://github.com/nikhilcherry/blank/actions/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776ab.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-c2410c.svg)](LICENSE)
[![Dependencies: none](https://img.shields.io/badge/dependencies-none-0d9488.svg)](pyproject.toml)

</div>

<p align="center">
  <img src="docs/img/report-hero.png" alt="The blank report for Flask: commit count, contributors, bus factor and a year of commit activity" width="100%">
</p>

```bash
blank scan ~/code/flask --open
```

That's the whole workflow. No config file, no database, no account, no network call.

---

## Why

Every repository has files that everyone is quietly afraid of. They are rarely the biggest
files, and they never show up in a linter. They are the files that are **both** tangled
**and** touched constantly — and the intersection is where defects actually cluster.

`blank` finds that intersection, and four other things a `git log` will not tell you:

| Question | What `blank` shows |
| --- | --- |
| *Which files are actually dangerous?* | Risk ranking — revisions × complexity, not size |
| *What breaks when I change this?* | Temporal coupling — files that always change together |
| *Who do we lose if someone leaves?* | Bus factor and per-file ownership |
| *What has nobody understood in years?* | Orphaned hotspots — risky code whose only author left |
| *Is this project healthy or crunching?* | A year of commit activity, at a glance |

Everything is derived from data you already have. Nothing is uploaded anywhere.

---

## Install

```bash
pip install blank-report          # provides the `blank` command
```

Or run it straight from a clone — there is nothing to build:

```bash
git clone https://github.com/nikhilcherry/blank
cd blank
python3 -m blank stats ~/code/some-repo
```

**Requirements:** Python 3.9+ and `git`. That's it. `blank` has **zero** third-party
dependencies — no numpy, no jinja, no charting library. The entire tool is the standard
library plus about 2,000 lines of Python.

---

## Usage

```
blank scan [PATH]        write a self-contained HTML report
blank stats [PATH]       print a summary in the terminal
blank hotspots [PATH]    rank files by revisions × complexity
blank authors [PATH]     contribution breakdown
blank coupling [PATH]    files that change together
blank check [PATH]       fail a build when thresholds are crossed
```

Every command accepts the same window flags:

| Flag | Effect |
| --- | --- |
| `--since DATE` | Only commits after `DATE` — anything git parses (`2024-01-01`, `18 months ago`, `1.year`) |
| `--max-commits N` | Stop after N commits, newest first |
| `--include-merges` | Count merge commits (off by default — they double-count changes) |
| `--no-color` | Plain output for logs and pipes (also honours `NO_COLOR`) |

### In the terminal

```bash
blank stats ~/code/flask
```

<p align="center">
  <img src="docs/img/terminal-stats.png" alt="blank stats output in a terminal, showing bus factor, languages, hotspots, coupling and contributors" width="88%">
</p>

Colour switches itself off when stdout is not a TTY, so piping into a file stays clean.

---

## What's in the report

### Hotspots: revisions × complexity

<p align="center">
  <img src="docs/img/hotspots-scatter.png" alt="Scatter plot of revisions against indentation complexity, with high-risk files in red at the top right" width="100%">
</p>

Each bubble is a file. Right means "revised often". Up means "deeply nested". Bubble size is
length. **Bottom-left is calm code. Top-right is where your incidents come from.**

Complex code that nobody touches is fine — leave it alone. Simple code that changes daily is
fine too — that's just an active module. The product of the two is the signal, and it is the
one metric here that reliably predicts where the next bug lands.

Change frequency is counted in **revisions, not lines changed**. Lines-changed looks like the
obvious choice and is a trap: a file's very first commit adds every line it has, so in a young
repository "churn" is just file length in disguise, and a fresh import reports every file as a
screaming hotspot. Revisions can't be inflated that way. Both axes are log-normalised to 0..1
before multiplying.

<p align="center">
  <img src="docs/img/risk-table.png" alt="Sortable risk ranking table listing files with risk score, commits, churn, complexity, lines, authors and days since last change" width="100%">
</p>

The table is sortable and filterable — click any column, type to narrow. It's plain
JavaScript embedded in the page; it works offline from a `file://` URL.

### Temporal coupling

<p align="center">
  <img src="docs/img/coupling.png" alt="List of file pairs that change together, with percentage and commit counts, tagged cross-module or same module" width="100%">
</p>

Files that keep appearing in the same commit are coupled whether or not they import each
other. A pair inside one module is usually fine. A **cross-module** pair at 80%+ means an
abstraction is leaking, or somebody's refactor stopped halfway.

Commits touching more than 40 files are excluded — a formatting sweep would otherwise
"couple" your entire repository to itself.

### Knowledge risk

<p align="center">
  <img src="docs/img/knowledge-risk.png" alt="Knowledge risk card listing risky files written almost entirely by one author who has since gone quiet" width="100%">
</p>

The intersection nobody tracks: a file that is **risky**, **written ≥80% by one person**, and
**that person hasn't committed in six months**. These are the files where the next change is
going to be archaeology.

### Composition and layout

<p align="center">
  <img src="docs/img/composition.png" alt="Donut chart of lines by language" width="45%">
  <img src="docs/img/treemap.png" alt="Squarified treemap of directory sizes by line count" width="52%">
</p>

Language mix, and a squarified treemap of where the lines actually live. Useful for the
"wait, our tests are bigger than our source" moment.

### Activity

<p align="center">
  <img src="docs/img/heatmap.png" alt="A year of daily commit activity as a heatmap grid" width="100%">
</p>

A year of daily commits. Sustained weekends and month-long silences both show up instantly.

### Light and dark

<p align="center">
  <img src="docs/img/report-hero-dark.png" alt="The same report rendered in dark theme" width="100%">
</p>

The page follows your system preference and remembers the toggle. Both themes ship in the
same stylesheet — there is no second build, and no flash on load.

**Want the whole thing?** [Full-page screenshot](docs/img/report-full.png) of the Flask report.

---

## Use it in CI

`blank check` turns the analysis into a build gate. It exits `1` when a threshold is
crossed, `0` when clean, and `2` when you forgot to give it any thresholds.

```bash
blank check . --max-risk 0.85 --min-bus-factor 2 --max-orphans 5
```

<p align="center">
  <img src="docs/img/terminal-check.png" alt="blank check failing with six findings: five files over the risk threshold and a bus factor below the minimum" width="82%">
</p>

```yaml
# .github/workflows/health.yml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0            # blank needs history — a shallow clone has none

- run: pipx install blank-report
- run: blank check . --max-risk 0.9 --min-bus-factor 2

- run: blank scan . -o blank-report.html
- uses: actions/upload-artifact@v4
  with:
    name: blank-report
    path: blank-report.html
```

> [!IMPORTANT]
> `fetch-depth: 0` is not optional. The default shallow checkout has one commit, and every
> history-derived metric will read as zero.

### Machine-readable output

```bash
blank scan . -o /dev/null --json - | jq '.hotspots[:3]'
```

```json
[
  {
    "path": "src/flask/sansio/app.py",
    "risk": 1.0,
    "commits": 508,
    "churn": 13482,
    "complexity": 5.089,
    "lines": 1013,
    "authors": 134,
    "main_author": "David Lord",
    "ownership": 0.275,
    "days_since_change": 89
  }
]
```

The JSON carries `summary`, `languages`, `hotspots`, `coupling`, `authors` and
`knowledge_risk`. Diff two runs to watch risk move over a quarter.

---

## How the metrics work

Every number here is a **proxy**. They are worth arguing with — which is why the definitions
live in exactly one file, [`blank/analyze.py`](blank/analyze.py).

**Revisions** — how many commits touched the file, following renames so a `git mv` doesn't
reset a file's history to zero. This is the change-frequency axis.

**Churn** — lines added + deleted. Reported in the table and the JSON as supporting detail,
but deliberately *not* used for ranking, for the reason above.

**Complexity** — mean indentation depth + 0.35 × max depth, in 4-space units. This is an
*indentation proxy*, not an AST metric. It cannot tell a nested comprehension from a nested
`if`. What it can do is work identically across 40 languages with no parser, and deeply
indented code is genuinely harder to hold in your head regardless of syntax. Files under 5
code lines score 0.

**Risk** — `normalise(revisions) × normalise(complexity)`, both `log1p`-scaled to 0..1 across
the repository. It is a *relative* ranking: 0.9 means "worst in this repo", not "worse than
some industry threshold". Comparing scores between two different repositories is meaningless.

Files revised fewer than twice score 0 — created-and-never-touched is zero evidence, not low
risk. When fewer than five files clear that bar, `blank` says so instead of printing a
confident-looking ranking built on nothing:

```
── hotspots  (revisions × complexity) ──────────────────────────────
  thin history — only 0 file(s) revised more than once, so the ranking is noise
  nothing to rank — no file has been revised since it was created.
```

**Coupling ratio** — `commits containing both / commits containing the rarer of the two`.
Reported at ≥4 shared commits and ≥35%.

**Bus factor** — the fewest authors whose combined lines-added cover half the codebase. It
measures *authorship*, not *understanding*: a team that reviews everything is far more
resilient than this number suggests, and a team that rubber-stamps is less.

**Ownership** — one author's share of the lines added to a file. Bulk commits (>40 files)
are excluded so a repo-wide reformat doesn't hand one person the whole codebase.

### What it deliberately does not do

- **No AST parsing.** Real cyclomatic complexity would need a parser per language. The
  indentation proxy correlates well enough to rank files, which is all that's needed here.
- **No blame-based ownership.** `git blame` over every file is minutes of work for a
  marginally better number. `blank` uses lines-added per author instead, and finishes a
  4,000-commit repository in about two seconds.
- **No judgement about tests.** A high-churn test file is often a *good* sign. Non-code
  files (Markdown, JSON, YAML, config) are counted in the composition charts but excluded
  from risk ranking, so a busy CHANGELOG never tops the list.

---

## Performance

Single pass over `git log --numstat`, one `os.walk`, everything else in memory.

| Repository | Commits | Files | Time |
| --- | --- | --- | --- |
| pallets/flask | 3,816 | 218 | ~2.0 s |

Narrow the window with `--since` or `--max-commits` on very large repositories — and note
that a shorter window is often *more* useful, since last year's hotspots matter more than
2014's.

---

## Development

```bash
git clone https://github.com/nikhilcherry/blank && cd blank
python3 -m unittest discover -s tests -t . -v     # 81 tests, no dependencies
python3 -m blank scan . --open                    # run it on itself
```

The layout:

| File | Does |
| --- | --- |
| [`blank/gitlog.py`](blank/gitlog.py) | Runs `git log`, parses `--numstat`, follows renames |
| [`blank/scan.py`](blank/scan.py) | Walks the tree: languages, line counts, indentation |
| [`blank/analyze.py`](blank/analyze.py) | Every metric definition, in one place |
| [`blank/charts.py`](blank/charts.py) | Server-rendered SVG: heatmap, donut, treemap, scatter |
| [`blank/render.py`](blank/render.py) | Assembles the HTML and JSON reports |
| [`blank/term.py`](blank/term.py) | Terminal output: colour, bars, sparklines |
| [`blank/cli.py`](blank/cli.py) | Argument parsing and command dispatch |

Charts are generated as SVG strings in Python, not drawn by JavaScript. That is why the
report opens instantly, works offline, prints correctly, and stays around 110 KB for a
4,000-commit repository.

Contributions welcome — especially better language detection, and a smarter complexity proxy
that stays parser-free.

---

## License

MIT — see [LICENSE](LICENSE).

<div align="center">
<sub>Screenshots generated from <a href="https://github.com/pallets/flask">pallets/flask</a>, 3,816 commits.</sub>
</div>

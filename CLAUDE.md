# CLAUDE.md

Guidance for Claude Code working in this repository.

`README.md` is how to run what exists. `ARCHITECTURE.md` is why it is built this
way. `ROADMAP.md` is what is not built yet. **This file is the map plus the
rules that must not be broken** — it does not repeat those three.

## What this is

Profile-driven KPI selection, synthetic data, dashboards and reports. One
deterministic pipeline:

```
CompanyProfile -> KPI selection -> data -> metrics -> facts -> insights -> artifacts
```

The three product modes (Samples / Survey / Bring-your-data) differ **only** in
how the `CompanyProfile` is produced. Nothing downstream knows which mode it
came from. If a mode-specific change needs to touch anything past
`profile/schema.py`, the contract has leaked — fix the leak instead.

## Commands

The interpreter lives in `.venv`. Path differs by platform — README examples are
Windows:

- Linux / macOS: `.venv/bin/python`
- Windows: `.venv/Scripts/python.exe`

Written `$PY` below.

```bash
$PY -m tests.stress            # full 23-case matrix — the real gate
$PY -m tests.stress --quick    # scale extremes only, much faster
$PY -m kpi_maker validate --profile samples/northwind_saas.json   # cheapest check
$PY -m kpi_maker run --profile samples/northwind_saas.json --out ./out
$PY -m kpi_maker serve --port 8000
$PY -m tools.build_pages --out site    # exactly what CI runs
```

There is **no pytest suite and no linter config**. `tests/stress.py` is the only
test surface: it asserts invariants across scale extremes rather than unit
behaviour. Run it after any change to selection, datagen, metrics or detectors.
`validate` is the fast smoke check while iterating.

## Where things live

| Concern | Module | Note |
|---|---|---|
| Input contract | `profile/schema.py` | pydantic; the enums here **branch the pipeline** |
| KPI definitions | `kpi/library/*.yaml` | data, not code — one pack per sector |
| `applies_when` evaluator | `kpi/expr.py` | AST whitelist, never `eval()` |
| Selection engine | `kpi/selection.py` | applicability → feasibility → scoring → coverage → tier caps |
| Synthetic data | `datagen/saas.py` | driver-based; asserts 12 accounting identities |
| Metric arithmetic | `metrics/engine.py` | `@metric("kpi_id")` registry — the **only** place arithmetic lives |
| Insight detectors | `insight/detectors.py` | 8 `_detector` functions, aggregated by `detect_all` |
| Chart tokens | `viz/theme.py` | validated palette; **max 3 categorical series** |
| Charts / static export | `viz/charts.py`, `viz/export.py` | |
| Deliverables | `render/` | dashboard, report(PDF), deck(PPTX), doc(DOCX), workbook(XLSX) |
| Pipeline orchestration | `cli.py::run_pipeline` | every caller (CLI, API, tests) goes through this |
| HTTP layer | `api/server.py` | thin wrapper over `run_pipeline`; engine has no HTTP imports |
| Web UI | `ui/` | vanilla JS, no build step |

## Invariants — do not break these

1. **The LLM never produces numbers or charts.** It fills the profile, plans,
   and writes prose from a pre-computed facts table. Everything numeric is
   deterministic Python. This is what makes Modes 1 and 2 cost zero tokens.
2. **Nothing renders on data that fails reconciliation.** `ReconciliationError`
   is a hard stop, not a warning. Never downgrade one to make a test pass.
3. **Every drop is recorded with a reason.** "Why isn't X on my dashboard?" must
   always have an answer. Never silently exclude a KPI or a metric — record it
   in `kpi_set.dropped` or `MetricResult.reason`.
4. **A KPI's arithmetic lives only in `metrics/engine.py`.** The YAML `formula`
   field is human documentation. Changing one without the other creates a lie.
5. **Three categorical colours maximum.** A fourth fails the contrast floor.
   More categories fold into "Other" or facet.
6. **Python 3.9 compatible.** Use `typing.Optional/List/Dict`, not PEP 604 `X | Y`.
7. **Benchmarks are illustrative placeholders.** Every `source` field must keep
   saying so. Do not present them as a licensed dataset.

## Common tasks

**Add a KPI** — add the YAML entry in `kpi/library/<sector>.yaml` (needs `id`,
`perspective`, `tier`, `timing`, `direction`, `serves_objectives`,
`applies_when`), then register `@metric("<id>")` in `metrics/engine.py`. YAML
alone yields a KPI that selects but never computes.

**Add a sector** — a new YAML pack plus a generator archetype registered in
`cli.py::GENERATORS`. Only `saas` exists (ROADMAP M2).

**Add a detector** — a `_name(...) -> List[Finding]` function in
`insight/detectors.py`, wired into `detect_all`. It must never emit a NaN or an
empty statement; `tests/stress.py` asserts this.

**Touch the profile enums** — adding a value without adding KPI coverage
silently narrows dashboards. Check `applies_when` expressions across the library.

## Environment traps

- **Non-ASCII paths break static chart export.** kaleido 0.2.1 mangles non-ASCII
  in a native subprocess (a username like `Meriç` fails with a misleading "not a
  valid URL or file path"). `viz/export.py` already stages files at an
  ASCII-safe location — that is why that code exists; don't remove it.
- **plotly is pinned `<6`** so kaleido 0.2.1 works. plotly 6 needs kaleido v1,
  which drives an external Chrome install. Do not bump it casually.
- **TLS interception**: if pip fails `CERTIFICATE_VERIFY_FAILED`, add
  `--trusted-host pypi.org --trusted-host files.pythonhosted.org`.
- `out/`, `runs/` and `site/` are generated and gitignored (~97 MB / ~22 MB).
  Never commit them.

## Conventions

- **Comments carry rationale, not narration.** Module docstrings explain the
  design decision and its constraint. Match that register — a comment restating
  what the line does is noise here.
- **Commit messages**: sentence case, imperative mood, no type prefixes or scope
  tags. e.g. `Replace the three-command install with a double-click launcher`,
  `Fix the Pages guard rejecting a valid .nojekyll`.
- CI (`.github/workflows/pages.yml`) runs the full pipeline on Linux and
  publishes the gallery on push to `main`. It is also the portability signal —
  kaleido export is the part most likely to differ off Windows.

# KPI Dashboard Maker

Profile-driven KPI selection, synthetic data, dashboards and reports.

The design rationale — question framework, KPI selection theory, the three
product modes, effort estimates — is in **[ARCHITECTURE.md](ARCHITECTURE.md)**.
This file is how to run what exists today.

## Status: M0 + M1 complete (SaaS)

Working end to end. Roadmap for everything else: **[ROADMAP.md](ROADMAP.md)**.

| Stage | Module | State |
|---|---|---|
| Profile schema + cross-block validation | `kpi_maker/profile/` | done |
| RunSpec contract + staged, cacheable pipeline | `kpi_maker/spec/`, `kpi_maker/pipeline/` | done |
| Formula engine (KPIs + calculated columns) | `kpi_maker/formula/`, `kpi_maker/prep/` | done |
| Studio — adjust any stage, re-run what changed | `ui/` | done |
| Two-tier reconciliation gate + pandera contract | `kpi_maker/contract/` | done |
| Cleaning ops + lineage log | `kpi_maker/prep/` | done — 19 ops |
| Real-data ingestion (5 shapes) | `kpi_maker/ingest/` | done |
| KPI library + sandboxed selection engine | `kpi_maker/kpi/` | done — SaaS pack (44 KPIs, 39% leading) |
| Synthetic data + reconciliation gate | `kpi_maker/datagen/` | done — SaaS |
| Metrics engine + facts table | `kpi_maker/metrics/` | done — 24 implemented |
| Deterministic insight detectors | `kpi_maker/insight/` | done — 8 detectors |
| Charts (validated palette) | `kpi_maker/viz/` | done — 9 forms, light + dark |
| Dashboard / workbook / CSV | `kpi_maker/render/` | done |
| PDF report / PPTX deck / DOCX | `kpi_maker/render/` | done — M1 |
| Survey + benchmark priors | `kpi_maker/survey/` | done — M3/M4 (SaaS) |
| Sample gallery (Mode 1) | `samples/` | done — M5, 4 companies |
| HTTP API | `kpi_maker/api/` | done |
| Web UI | `ui/` | done — M8 (vanilla JS, no build step) |
| Cross-sector fallback pack | `kpi_maker/kpi/library/general.yaml` | done — 19 KPIs, every sector runs |
| More sectors (M2) | `kpi_maker/datagen/`, `kpi_maker/kpi/library/` | partial — 2 of 10 have their own archetype and pack; the other 8 run on the nearest archetype and the cross-sector pack, and say so |
| AI Builder (M7) | `kpi_maker/ai/` | partial — planner, narrator and the number check ship; the conversational front door does not |

## Run it

**Double-click `start.bat`** (Windows) or **`start.command`** (macOS, Linux).
That is the whole procedure. It finds Python, builds the environment, installs
the dependencies, starts the server and opens your browser — and on a network
that intercepts TLS it retries pip with the trusted-host flags rather than
dying on a certificate error. Only the first run is slow.

Needs Python 3.11 or newer. If it is missing, the launcher says so and where to
get it.

<details>
<summary>By hand, if you would rather</summary>

```bash
py -3 -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
./.venv/Scripts/python.exe -m kpi_maker serve --open
```

Behind a TLS-intercepting proxy, pip needs
`--trusted-host pypi.org --trusted-host files.pythonhosted.org`.
</details>

The app is then at **http://127.0.0.1:8000**, and
[the hosted page](https://merogith.github.io/MasterBI/) will find it too.
Three modes on the home screen:

| Mode | State | Cost |
|---|---|---|
| **1 · Try a sample** | Working — 4 curated companies | Free |
| **2 · Build your own** | Working — 14 core questions, 5 optional | Free |
| **3 · Bring your data** | Working — read, profile, clean, map, run | Free |
| **Surprise me** | Working — random self-consistent company | Free |

Results open in a workspace with five tabs: Overview (findings), Dashboard
(embedded), Scorecard (filterable), Data (every fact table), Downloads.

## The hosted app (GitHub Pages)

**https://merogith.github.io/MasterBI/** — one URL with two capabilities.

| | Nothing running locally | Local server running |
|---|---|---|
| Three sample companies | yes, pre-rendered | yes |
| Build your own · Bring your data · Surprise me | explained, not offered | yes |
| Where runs are stored | nowhere — read only | your `runs/` folder |
| Past runs | — | Recent runs, across restarts |

The page probes `127.0.0.1` on ports 8000, 8001 and 8080 at startup. Finding a
server, it proxies every call there and the header pill turns green; finding
nothing, it serves the pre-rendered samples. A loopback address is a
trustworthy origin, so HTTPS→`http://127.0.0.1` is not mixed content; the
server answers the Private Network Access preflight Chrome additionally wants.

Nothing needs configuring and nothing is uploaded — the hosted page is the
front end, the user's own machine is the engine, and their data never leaves it.

Three constraints shaped the design:

- **One front end.** `tools/static_shim.js` patches `fetch` rather than forking
  the UI, so `ui/app.js` is byte-identical in all three contexts. A hosted copy
  and a local copy would have drifted within a release.
- **Detection never blocks.** `app.js` loads immediately and the probe catches
  up behind it. Blocking on it cost 6s of dead page whenever nobody was running
  anything locally, which is the common case.
- **No silent switching mid-session.** A pre-built run's id exists only in the
  hosted build. If the probe lands after one is already open, the pill offers
  the switch instead of taking it, because routing the next call to the local
  server would 404 on a page that looks fine.

Build it locally exactly as CI does:

```bash
./.venv/Scripts/python.exe -m tools.build_pages --out site
```

## Deploy a live instance

`render.yaml` is a Render blueprint. Connect the repo once (render.com → New →
Blueprint → `merogith/MasterBI` → Apply) and every push to `main` redeploys
automatically. It serves the same FastAPI app, so the hosted URL behaves
exactly like `serve` does locally.

Two free-tier facts worth knowing: the service sleeps after 15 minutes idle
(~30s to wake), and the disk is ephemeral — `runs/` is wiped on each deploy, so
past runs vanish. Both are fine for testing; neither is acceptable for
production storage.

The equivalent command by hand, on any host:

```bash
uvicorn kpi_maker.api.server:app --host 0.0.0.0 --port $PORT
```

## Adjusting a run

`CompanyProfile` says who the company is. **`RunSpec`** says what the pipeline
should do about it — source, cleaning, model, metrics, analysis, design,
outputs. Every field is optional, and every default means "derive it the way
the pipeline always did", so an empty spec reproduces the original behaviour
exactly.

```jsonc
{
  "profile": { /* … the usual profile … */ },
  "source":  { "generator": { "seed": 7, "history_months": 48 } },
  "metrics": {
    "excluded":  ["cac_payback_months"],
    "pinned":    ["quick_ratio"],
    "overrides": { "gross_margin_pct": { "target": 0.85 } }
  },
  "analysis": { "disabled": ["channel_efficiency"], "max_findings": 12 },
  "outputs":  { "artifacts": ["dashboard", "facts_csv"] }
}
```

```bash
python -m kpi_maker run  --spec my_spec.json --out ./out
python -m kpi_maker run  --profile samples/northwind_saas.json --only dashboard
python -m kpi_maker plan --spec my_spec.json --out ./out   # what would rebuild?
```

Stages declare which upstream stages they need and which spec sections they
read, and those two things form the cache key. Change the theme and two stages
rebuild; exclude a KPI and the data generation is skipped entirely.

Picking your outputs is the same mechanism, and it is the biggest speedup
available: rendering is ~80% of a run and the static PNG export alone is ~37%,
so `--only dashboard,facts_csv` takes **1.3s against 7.5s** for everything.

Over HTTP, the same controls:

| Endpoint | |
|---|---|
| `GET /api/runs/{id}/spec` | what this run actually did |
| `PATCH /api/runs/{id}/spec` | deep-merge one field |
| `GET /api/runs/{id}/plan` | which stages a re-run would rebuild, and roughly how long |
| `POST /api/runs/{id}/rerun` | rebuild only what changed |
| `GET /api/catalog/options` | artifacts, detectors and themes the engine supports |

`POST /api/runs` also accepts a `spec`, so a sample can be launched already
customised rather than only inspected afterwards.

## Your own KPIs and calculated fields

Any run opens in the **Studio** — a panel per pipeline stage, with a bar that
says what your change costs before you commit to it ("9 stages to rebuild,
about 1s"). Every entry point lands there, presets included.

KPIs are defined by formula, in a spreadsheet-like language:

```
SAFE_DIV(SUM(marketing.spend), SUM(marketing.leads))    a cost per lead
YOY(monthly_financials.arr) + TTM(fcf_margin)           compose existing KPIs
SUM(mrr_movements.delta_mrr, movement_type='churn')     aggregate with a filter
```

Four fields make a KPI: **name, formula, unit, direction**. Unit drives
formatting and direction drives the RAG colour; the rest default and sit behind
an "advanced" accordion. User KPIs are marked as such in the appendix rather
than presented as reviewed library metrics, and one sharing an id with a
library KPI replaces it — recorded, not silent.

The same language writes **calculated columns** on a fact table
(`final_acv - initial_acv` on `customers`), which KPIs can then aggregate over.
Time functions are refused there, because a row has no time axis.

Three table grains, three behaviours — this is the rule worth knowing:

| | |
|---|---|
| `monthly_financials`, `pipeline`, `product_usage`, `sales_capacity` | already monthly; reference a column directly |
| `marketing`, `headcount`, `mrr_movements` | several rows per month; must go through an aggregate, which groups by month |
| `customers` | one row per entity, no month at all; usable in a calculated column, never as a KPI |

Referencing `marketing.spend` bare therefore fails with *"several rows per
month, wrap it in SUM()"* rather than quietly picking an aggregation for you.

Nothing reaches `eval()`. Expressions are parsed with `ast`, checked whole
against an explicit node whitelist before anything runs, and evaluated by a
resolver that looks names up in a mapping it built itself.

## Run from the CLI

```bash
./.venv/Scripts/python.exe -m kpi_maker run --profile samples/northwind_saas.json --out ./out
```

Validate a profile and see the KPI selection without generating anything:

```bash
./.venv/Scripts/python.exe -m kpi_maker validate --profile samples/northwind_saas.json
```

Change the company by editing the profile JSON, or re-roll the same company's
data with a different seed:

```bash
./.venv/Scripts/python.exe -m kpi_maker run --profile samples/northwind_saas.json --out ./out --seed 7
```

## Output

```
out/
  dashboard.html     self-contained interactive dashboard (Plotly inlined, works offline)
  report.pdf         15-page executive report — the deliverable you email
  deck.pptx          15-slide board deck, one message per slide title
  report.docx        same content, editable, real Word heading styles
  workbook.xlsx      Summary · Tracker (live formulas) · Definitions · Assumptions · Findings · raw tabs
  charts/*.png       print-resolution chart exports
  facts.csv          the compact, LLM-safe view of every computed KPI
  findings.json      detector output
  profile.json       the resolved input profile
  kpi_set.json       which KPIs were selected, why, and what was dropped
  data/*.csv         six normalized fact tables
```

## The front end, and the rewrite in progress

What a user gets today is `ui/` — one 2,000-line `app.js`, no build step, and
no URL for anything: every screen change flips a `hidden` attribute, so Back
leaves the app and no run can be linked to.

`web/` is the replacement, and it is **what the server now serves**: Vite,
TypeScript and Preact, with real routing. Every screen is ported — home,
samples, survey, Bring-your-data, running, results, the history drawer and all
eight Studio panels, including the flows inside them (adopting an upload, the
cleaning-step and calculated-column editors, the add-a-KPI formula editor,
per-KPI targets, the live brand preview, and the AI estimate and plan review).

```bash
npm --prefix web ci && npm --prefix web run build   # → kpi_maker/ui_dist/
python -m uvicorn kpi_maker.api.server:app
```

`ui/` has not been deleted, and not as a fallback anyone should rely on: the
GitHub Pages demo is still built from it by `tools/build_pages.py`, which
patches its `index.html` and serves `app.js` behind `static_shim.js`. Deleting
it today would take the hosted demo with it, so it goes when the Pages bundle is
built from `web/` instead. `MASTERBI_UI=legacy` selects it meanwhile, and it is
also what serves a checkout where the bundle was never built.

Nothing at runtime needs Node: the build produces static files that FastAPI
serves and that the packaged executable will bundle. `web/src/lib/api.ts`
deliberately leaves API paths bare, because `tools/static_shim.js` replaces
`window.fetch` on the hosted demo — only artifact `href`s resolve against
`window.KPI_FILES_BASE`.

Both front ends are graded by the same browser tests, which is what makes this
a port rather than a second product.

## Testing

```bash
./.venv/Scripts/python.exe -m tests.stress          # 23-case matrix
./.venv/Scripts/python.exe -m tests.stress --quick  # scale extremes only
./.venv/Scripts/python.exe -m tests.spine           # RunSpec + stage graph
./.venv/Scripts/python.exe -m tests.formula         # the formula language
./.venv/Scripts/python.exe -m tests.ingest          # ingestion, cleaning, the gate
./.venv/Scripts/python.exe -m tests.design          # colour, sections, exhibits
./.venv/Scripts/python.exe -m tests.sector          # archetypes and the vacuity guard
./.venv/Scripts/python.exe -m tests.ai              # the AI layer, entirely offline
```

All of them, plus the pytest-native suites, run under one command — this is
what CI runs on every push:

```bash
python -m pytest -q
```

`tests/test_smoke_ui.py` is the only test that opens the product in a browser.
It drives Chromium through the path that has to keep working — pick a sample,
watch it run, land on results, edit the spec in the Studio, re-run it, then find
and reopen the run in history — and fails on any uncaught JS error, which is
otherwise reported nowhere. It needs a browser:

```bash
pip install playwright && python -m playwright install chromium
```

Without those it skips rather than fails, so the three-OS matrix does not need
one. It runs the real server as a subprocess against a throwaway run directory,
via `MASTERBI_RUNS_DIR` — the same environment override the desktop build uses
to keep runs in a user data folder instead of beside the executable.

`tests/ai.py` needs no API key and makes no network call — the client is
swapped for a transcript player at one module seam. A gate nobody can run is
not a gate. Its central assertions are that with AI off no client is ever
*constructed*, and that a single invented figure in the prose is caught, fed
back once, and then costs that section its paragraph while the rest of the
report stands.

`tests/spine.py` asks a different question from `stress.py`: not "does the
product lie?" but "does adjusting the pipeline do exactly what it says, and
nothing else?" It asserts that an empty spec is neutral, that editing one
section rebuilds exactly the stages that read it (too few and the edit
silently does nothing; too many and the studio is slow for no reason), that a
warm partial re-run and a cold full run produce identical artifacts, and that
bad input is refused rather than ignored.

`tests/stress.py` is not a unit-test suite — it is a "does the product lie?"
suite. It runs the pipeline across scale extremes (a 4-person / $220k startup, a
21,000-person / $5B platform, a 3-customer book), every stage, every objective,
every audience and both sales motions, then asserts invariants that must hold
for any company:

- every accounting identity reconciles
- no metric falls outside its plausible band (a 2,005-month cash runway is a
  bug, not an unusual company)
- GRR never exceeds NRR; customers × ARPA reconciles to MRR
- no finding leaks a NaN or an empty statement
- Balanced Scorecard coverage and the leading-indicator floor are met

## The two rules the codebase enforces

**1. The LLM never produces numbers or charts.** It does two things: propose a
patch to the run's configuration, which you review change by change and which
cannot touch the company profile, and write the connective prose in a report
section from a pre-computed facts table. Every figure in that prose is checked
against the facts table by `ai/verify.py` before it is printed, and prose that
fails is discarded rather than shown. Everything numeric is deterministic
Python. `spec.ai.enabled` is **off by default**, so a preset, a survey run or a
CLI invocation costs **zero tokens** unless you turn it on.

**2. Nothing renders on data that fails reconciliation.** `kpi_maker/contract/`
holds the identities as a registry, tagged by tier and by archetype. Tier 1 is
definitional arithmetic — the P&L ties, ARR ties to the MRR book, checkouts
never exceed sessions — and is a hard stop whatever the source. Tier 2 compares
the data against what you said about yourself, and is fatal only for generated
data, where hitting the profile is the generator's job. A *generated* archetype
that contributes no structural checks is refused outright, so a new sector
cannot pass the gate on an empty set.

## Environment traps worth knowing

- **Non-ASCII paths break static chart export.** kaleido 0.2.1 hands the
  plotly.js path to a native subprocess that mangles non-ASCII characters on
  Windows, so a username like `Meriç` fails with a misleading "not a valid URL
  or file path". `viz/export.py` stages the file at an ASCII-safe location
  automatically — no action needed, but that is why the code exists.
- **TLS interception.** If pip fails with `CERTIFICATE_VERIFY_FAILED`, add
  `--trusted-host pypi.org --trusted-host files.pythonhosted.org`.
- **plotly is pinned below 6.0** so kaleido 0.2.1 can be used. plotly 6 needs
  kaleido v1, which drives an external Chrome install.

## Known gaps

- **Benchmarks are illustrative placeholders**, not a licensed dataset. Every
  `source` field says so and the caveat is rendered into the dashboard appendix
  and the workbook. Replace before any commercial use. This is the one item
  that genuinely blocks commercial launch.
- **A sharply declining book cannot be modelled.** The customer-count target and
  a steep revenue decline are jointly unsatisfiable, so a company that reports
  itself as shrinking lands near flat. The reconciliation output states the gap
  explicitly rather than presenting a number that contradicts the user.
- `nps`, `employee_enps` and support metrics need survey/helpdesk feeds the
  generator does not fabricate, so they drop out at selection with a recorded
  reason instead of showing invented scores.
- **Two sectors have their own KPI pack** — SaaS and e-commerce. The other
  eight run on the cross-sector `general` pack and the nearest generator
  archetype, and every run says so in its own output rather than presenting a
  borrowed scorecard as a native one. Adding a sector is a YAML file plus a
  generator archetype (ROADMAP M2).

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
| KPI library + sandboxed selection engine | `kpi_maker/kpi/` | done — SaaS pack (44 KPIs, 39% leading) |
| Synthetic data + reconciliation gate | `kpi_maker/datagen/` | done — SaaS |
| Metrics engine + facts table | `kpi_maker/metrics/` | done — 24 implemented |
| Deterministic insight detectors | `kpi_maker/insight/` | done — 8 detectors |
| Charts (validated palette) | `kpi_maker/viz/` | done — 9 forms, light + dark |
| Dashboard / workbook / CSV | `kpi_maker/render/` | done |
| PDF report / PPTX deck / DOCX | `kpi_maker/render/` | done — M1 |
| Survey + benchmark priors | `kpi_maker/survey/` | done — M3/M4 (SaaS) |
| Sample gallery (Mode 1) | `samples/` | done — M5, 3 companies |
| HTTP API | `kpi_maker/api/` | done |
| Web UI | `ui/` | done — M8 (vanilla JS, no build step) |
| More sectors (M2) | — | not started |
| Ingestion (M6) | `api/upload` | profiling only; no mapping |
| AI Builder (M7) | — | not started |

## Setup

The repo ships a venv-less checkout; create one and install:

```bash
py -3.9 -m venv .venv && ./.venv/Scripts/python.exe -m pip install -r requirements.txt
```

If your network intercepts TLS (corporate proxy), pip needs:

```bash
./.venv/Scripts/python.exe -m pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt
```

## Run the app

```bash
./.venv/Scripts/python.exe -m kpi_maker serve
```

Then open **http://127.0.0.1:8000**. Three modes on the home screen:

| Mode | State | Cost |
|---|---|---|
| **1 · Try a sample** | Working — 3 curated companies | Free |
| **2 · Build your own** | Working — 14-question survey | Free |
| **3 · Bring your data** | Column profiling only; mapping + AI not connected | Free |
| **Surprise me** | Working — random self-consistent company | Free |

Results open in a workspace with five tabs: Overview (findings), Dashboard
(embedded), Scorecard (filterable), Data (every fact table), Downloads.

## The static gallery (GitHub Pages)

**https://merogith.github.io/MasterBI/** runs the real UI with the three sample
companies pre-rendered — dashboard, scorecard, all eight fact tables and every
download. `.github/workflows/pages.yml` rebuilds it on each push to `main`.

Pages serves files and cannot execute Python, so only Mode 1 can work there.
`tools/build_pages.py` pre-renders those runs and writes the same JSON shapes
the API returns; `tools/static_shim.js` patches `fetch` so `ui/app.js` is
served byte-identical in both places rather than forked into a second front end
that would drift. Modes 2, 3 and Surprise return an explanation instead of a
network error.

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

## Testing

```bash
./.venv/Scripts/python.exe -m tests.stress          # 23-case matrix
./.venv/Scripts/python.exe -m tests.stress --quick  # scale extremes only
```

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

**1. The LLM never produces numbers or charts.** It fills the profile, plans, and
writes prose from a pre-computed facts table. Everything numeric is deterministic
Python. Modes 1 and 2 therefore cost zero tokens.

**2. Nothing renders on data that fails reconciliation.** `datagen/saas.py`
asserts twelve accounting identities — the P&L ties, ARR ties to the MRR book,
the customer count and blended ACV match the profile — before any artifact is
written. `ReconciliationError` is a hard stop, not a warning.

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
- Only the SaaS pack exists. Adding a sector is a YAML file plus a generator
  archetype (ROADMAP M2).

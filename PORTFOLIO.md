# MasterBI portfolio edition

A single-user BI showcase: prebuilt case studies in the browser, with uploads,
analysis and OpenAI assistance on your own computer. No hosted database or paid
backend is required for the gallery.

## Start locally

Use Python 3.11+ and Node for the initial frontend build:

```sh
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell instead: .venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-ai.txt
npm --prefix web ci
npm --prefix web run build
python -m uvicorn kpi_maker.api.server:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000/explore**. The dashboard, Studio, demonstrations and
exports work without an API key. To enable AI, set `OPENAI_API_KEY` in the server's
environment before starting it. Do not put the key in frontend code or commit it.

## A five-minute demonstration

1. Open the Pokemon VGC example. It simulates five events, 32 entries per event
   and seven rounds. Wins and losses balance across each event. The headline
   rate is `sum(wins) / sum(games)`, with entry counts alongside each team style.
   These are synthetic records, not actual tournament results or metagame claims.
2. Open Studio. Add total wins, switch the grouping to event, and apply. Undo
   returns the exact previous analysis. The source rows stay unchanged.
3. Download PDF and PowerPoint. Both use the same computed snapshot as the UI;
   the PowerPoint chart is native and editable.
4. Upload an unfamiliar CSV or Excel workbook. The first analysis counts rows;
   it does not infer that a column called `amount` is revenue or MRR. Review
   detected types and the first eight rows in Data.
5. Describe the row grain, units and goal in Studio or chat. Ask the assistant
   to propose an average, filter or grouping. Review the proposed settings, apply
   them, and export the updated results. Reload to show that chat and edits persist.

The existing business-report workflow remains at `/data`, with the original
sector-specific galleries, Balanced Scorecard KPIs and nine artifact types.
The survey supports multiple secondary objectives, a free-text reader question
and output selection. Surprise generates a seeded survey across supported sectors.

## Scope and contracts

| Area | Implemented scope |
|---|---|
| Inputs | One to five CSV/XLSX files; all nonempty workbook sheets |
| Limits | 10 MB/file, 30 MB total, 20 tables, 100,000 rows and 100 columns/table |
| Modeling | One active table per analysis; explicit column selections; tables remain separate |
| Metrics | Count, distinct count, sum, mean, median, min, max, paired ratio of sums; up to six metrics |
| Cleaning | Reversible whitespace trimming and exact duplicate removal |
| Filters | Equality, inequality, literal contains, numeric bounds; up to eight filters |
| Visuals | Bar, line, table; original/day/month/year grouping; editable color |
| Outputs | Executive PDF, editable PowerPoint charts, standalone HTML, displayed-group CSV |
| Persistence | Original table values, analysis, last 30 undo states, last 40 chat messages, estimated AI spend |

All headline metrics use every included row. Charts and grouped exports show at
most 20 groups, explicitly labeled. Non-line charts rank groups by the first
metric; line charts retain sorted group order. Use ISO dates for unambiguous date
grouping. No automatic business mappings, joins, imputation, prediction or causal
claims are made in the general-data workspace.

Numeric aggregations exclude missing, invalid and infinite values. An empty sum
is unavailable. Ratios use rows with both numerator and denominator present;
a zero denominator is unavailable. Numeric identifiers remain strings on upload.
The existing business pipeline also refuses to derive total operating expense
from an incomplete set of expense categories, and keeps zero-revenue margin
unavailable.

## OpenAI and cost

| Choice | API model | Input / output per million tokens |
|---|---|---|
| Standard (default) | `gpt-5.6-luna` | $0.20 / $1.20 |
| Advanced (manual) | `gpt-6-astra` | $10.00 / $50.00 |

Standard-processing prices were checked against the
[OpenAI pricing documentation](https://developers.openai.com/api/docs/pricing)
on 2026-09-12. Prices can change; the displayed amounts are estimates, not an
invoice. An older saved run with an Anthropic model must be switched to Standard
or Advanced in Studio before using AI.

The adapter uses the Responses API with strict structured output and `store=False`.
Keys stay server-side. General-workspace chat sends column metadata, computed
aggregates, the current analysis and the last eight messages; it does not send
whole source files. Group labels and user-entered context can contain personal
information. `store=False` does not itself mean zero provider retention.

Chat asks for a cost estimate before sending, defaults to a $1 cumulative project
budget, and limits a response to 3,000 output tokens. A separate per-call estimate
limit defaults to $0.50 (`MASTERBI_AI_MAX_CALL_USD` on the server). Input estimates
use UTF-8 byte length plus overhead; no paid API call is needed for an estimate.
Cached input is counted once. Automatic SDK retries are disabled. Limits are
local application guards, not provider billing guarantees or an account-wide
monthly cap. Failed/incomplete model responses never apply changes.

## Architecture and privacy

`kpi_maker/explore/engine.py` validates a typed analysis plan and computes results
with pandas. `api.py` persists projects under `runs/_projects`, serializes paid
chat calls, checks revisions on edits, and validates all AI proposals before
showing them. `exports.py` consumes that same result contract. It uses vector
PDF graphics and native PowerPoint charts, so these exports do not need Kaleido.

Projects are local files with no authentication layer. Keep the API bound to
loopback. Browser access is limited to local origins and the project's GitHub
Pages origin; explicit extra origins use `MASTERBI_ALLOWED_ORIGINS`. Upload paths
and artifact paths are contained in their workspace directories. The API supports
project deletion, including its source tables and chat, through
`DELETE /api/explore/projects/{id}`.

The Pages build freezes the retail, SaaS and Pokemon examples and all four
exports through `tools/build_explore_examples.py`. It never calls OpenAI. It also
retains the existing seven business case studies. Use the existing Pages workflow
to publish after reviewing and merging the branch.

## Deliberately deferred

Multi-table relationship editing and joins; large enterprise dumps; database/SaaS
connectors; arbitrary generated Python or SQL; collaboration and hosted accounts;
native phone/desktop releases; clinical interpretation. The web layout stacks on
smaller screens, but mobile editing and browser rendering still need device QA.
The existing advanced business Studio and the new general-data Studio share the
OpenAI adapter but currently have different analysis contracts.

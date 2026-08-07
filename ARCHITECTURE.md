# KPI Dashboard Maker — Architecture & Build Plan

## 0. The one idea that makes this tractable

Everything in the product reduces to a single JSON document — the **CompanyProfile** — plus a
deterministic pipeline that turns it into artifacts.

```
                 ┌─ Mode 1: Samples      → load a pre-written profile.json
CompanyProfile ←─┼─ Mode 2: Survey       → build profile from ~14 answers + benchmark defaults
                 └─ Mode 3: AI Builder   → Claude fills profile from uploaded data / chat

CompanyProfile → KPI Selection → Data Model → Synthetic or Real Data → Metrics Engine
              → Facts Table → Chart Specs → [Dashboard | PDF | XLSX | DOCX | PPTX]
                            → Narrative (LLM, grounded on Facts Table only)
```

The three menu options are **not three products**. They are three ways to produce the same JSON.
Build the pipeline once; the modes are thin.

### Hard rule: the LLM never produces numbers or charts

| LLM does | Deterministic code does |
|---|---|
| Ask intake questions, fill the profile schema | Select KPIs from the library |
| Choose which KPI packs / sections apply | Generate or ingest the data |
| Write narrative from a computed facts table | Compute every metric |
| QA the narrative against the facts | Render every chart and export |

This is what makes results reproducible, cheap in tokens, and defensible to a client. It also answers
your "optimized for data used" point: the model sees the profile and an aggregated facts table
(~2–5 KB), never the row-level dataset.

---

## 1. What we must know about a business (the input model)

This is the "what data do we need" question. Grouped into blocks; each field either **branches the
KPI set**, **shapes the data model**, or **sets a benchmark prior**. If a field does none of those
three, it does not exist.

### 1.1 CompanyProfile schema (v1)

```jsonc
{
  "identity": {
    "name": "Acme Yapı A.Ş.",
    "country": "TR",                   // drives currency, fiscal calendar, VAT, holidays, locale
    "currency": "TRY",
    "reporting_currency": "EUR",       // multi-currency is common outside the US
    "fiscal_year_start": "01-01",
    "language": "tr",
    "entity_count": 1,
    "sites": ["Istanbul", "Bursa"]
  },
  "industry": {
    "taxonomy": "NACE",                // NACE (EU/TR) | NAICS (US) | internal
    "code": "C25.1",
    "internal_sector": "manufacturing.metal_fabrication",
    "vertical_tags": ["construction_supply", "export"]
  },
  "business_model": {
    "type": "manufacturing",           // saas|ecommerce|retail|manufacturing|services|marketplace|
                                       // distribution|hospitality|healthcare|logistics|fintech|nonprofit
    "customer_type": "B2B",            // B2B|B2C|B2B2C|B2G
    "revenue_model": ["project", "recurring_supply"],
                                       // subscription|transactional|project|licensing|ads|commission|usage
    "sales_motion": "field",           // self_serve|inside|field|channel|retail_footfall|tender
    "delivery_model": "make_to_order"  // make_to_stock|make_to_order|engineer_to_order|service|digital
  },
  "size": {
    "headcount_total": 180,
    "headcount_by_function": { "production": 110, "sales": 18, "marketing": 6,
                               "engineering": 14, "finance_admin": 12, "logistics": 20 },
    "revenue_band": "10-50M",
    "stage": "established",            // pre_revenue|early|growth|established|mature|turnaround
    "age_years": 22,
    "ownership": "family"              // family|pe_backed|vc_backed|public|state|coop
  },
  "market": {
    "geographies": [{"country":"TR","share":0.6},{"country":"DE","share":0.25},{"country":"IT","share":0.15}],
    "segments": ["OEM", "distributors", "direct_projects"],
    "customer_count": 240,
    "concentration_top10_pct": 0.55,   // huge driver of risk KPIs
    "avg_order_value": 42000,
    "seasonality": "construction"      // none|retail_q4|summer_peak|construction|academic|agricultural
  },
  "products": [
    {"line": "structural_brackets", "revenue_share": 0.45, "gross_margin": 0.28, "unit_economics": {...}},
    {"line": "custom_fabrication",  "revenue_share": 0.35, "gross_margin": 0.34},
    {"line": "spare_parts",         "revenue_share": 0.20, "gross_margin": 0.51}
  ],
  "financials": {
    "revenue": 32000000, "gross_margin_pct": 0.31, "ebitda_pct": 0.11,
    "opex_split": {"sales":0.06,"marketing":0.02,"rnd":0.03,"ga":0.05,"logistics":0.04},
    "dso_days": 78, "dio_days": 64, "dpo_days": 45,
    "capex_pct_revenue": 0.04, "net_debt": 4200000
  },
  "operations": {
    "channels": ["direct_sales","distributor","tender_portal"],
    "supply_chain": "domestic_plus_import",
    "capacity_utilization": 0.72,
    "shift_pattern": "2x8h"
  },
  "org_culture": {
    "data_maturity": "spreadsheets",   // none|spreadsheets|bi_tool|warehouse|data_team
    "decision_cadence": "monthly",     // daily|weekly|monthly|quarterly
    "planning_horizon": "annual",
    "risk_appetite": "conservative",
    "kpi_experience": "low"            // → controls how many KPIs we dare show
  },
  "intent": {
    "primary_objective": "margin_expansion",
      // growth|margin_expansion|retention|cash_liquidity|quality|market_entry|
      // turnaround|fundraise|exit_prep|digital_transformation|cost_reduction|compliance
    "secondary": ["working_capital"],
    "horizon_months": 12,
    "audience": "board"                // board|exec|department_head|investor|bank|operational_team
  },
  "data_availability": {
    "has": ["erp_sales","gl","hr_headcount"],
    "missing": ["crm_pipeline","web_analytics","nps"]
  },
  "_provenance": {                     // every field tagged: how did we get it?
    "financials.gross_margin_pct": "benchmark_default:NACE_C25.1_TR_p50",
    "market.customer_count": "user_survey",
    "products": "ai_inferred_from_upload:sales_2024.xlsx"
  }
}
```

`_provenance` is not optional. It is what lets the report footnote say *"Gross margin assumed at
sector median (31%) — replace with actuals"* instead of quietly lying. It also drives a
**confidence score** on every output page.

### 1.2 The survey (Mode 2) — question design

Best practice from consulting diagnostics + survey methodology:

- **Progressive disclosure.** 14 core questions max before the user sees a result. Anything else is
  an optional deep-dive that *unlocks more precise KPIs* — show the user what each extra block buys them.
- **Every question must branch something.** If two different answers produce the same dashboard,
  delete the question.
- **Never a blank text box for a number.** Bands only (`<1M`, `1–10M`, `10–50M`...). People answer
  bands honestly and abandon free-text fields.
- **Every question has "I don't know / use sector average."** That fills from the benchmark prior and
  tags provenance. No dead ends.
- **Ask intent before you ask facts.** Objective is the strongest single predictor of which KPIs matter.

```
Q1   Country / primary market                    → currency, calendar, benchmark region
Q2   Industry (searchable, NACE/NAICS-backed)    → KPI packs, data model
Q3   What do you sell?  goods|services|software|platform|mixed
Q4   Who buys?          B2B|B2C|B2B2C|B2G
Q5   How do you charge? subscription|per-order|per-project|commission|usage|mixed
Q6   Headcount band                              → per-FTE metrics, org KPIs
Q7   Revenue band                                → benchmark cohort
Q8   Company stage                               → growth vs efficiency weighting
Q9   #1 objective for the next 12 months         → KPI weighting (biggest branch in the system)
Q10  Who reads this?  board|exec|dept|investor|bank
Q11  How often do you review numbers?            → dashboard cadence, trend granularity
Q12  Data maturity today                         → realistic vs aspirational KPI tier
Q13  Biggest pain right now (multi-select)       → the "diagnostic" section of the report
Q14  Which functions do you want covered?        → dashboard tabs

+ optional blocks (each ~4 questions): Sales · Marketing · Ops/Production ·
  Finance/Working capital · People/HR · Customer/Retention
+ "Surprise me" → sample the entire profile from benchmark distributions, seeded, reproducible
```

---

## 2. Picking the right KPIs (the academic / best-practice core)

This is the part that determines whether the output looks like a consulting deliverable or a
metrics dump. Four layers, applied in order.

### 2.1 Layer 1 — Coverage: Balanced Scorecard (Kaplan & Norton, 1992/1996)

Force representation across four perspectives so the dashboard is never all-financial:

| Perspective | Question it answers |
|---|---|
| Financial | How do we look to shareholders? |
| Customer | How do customers see us? |
| Internal Process | What must we excel at? |
| Learning & Growth | Can we continue to improve and create value? |

Rule: **min 2, max 7 KPIs per perspective.** A dashboard with 40 metrics has no dashboard.

### 2.2 Layer 2 — Causality: value driver tree / strategy map

Decompose one **North Star** into drivers, mathematically, not thematically. DuPont for the
financial spine:

```
ROIC = NOPAT margin × Capital turnover
        │                  │
        ├ Gross margin     ├ Receivables (DSO)
        │  ├ Price/mix     ├ Inventory (DIO)
        │  └ Unit cost     └ Fixed asset utilization
        └ Opex ratio
```

Every L2 KPI must attach to a parent node. This gives you: (a) the dashboard's drill-down structure
for free, (b) "why did the North Star move" waterfall charts for free, (c) a defensible answer to
*"why is this metric on here?"*

### 2.3 Layer 3 — Timing: leading vs lagging

Each KPI is tagged `leading` or `lagging`. Target mix ≈ **40% leading / 60% lagging.** A dashboard of
only lagging indicators is a post-mortem, not a tracker. Pipeline coverage is leading; revenue is
lagging. Training hours is leading; attrition is lagging.

### 2.4 Layer 4 — Definition quality: the KPI record sheet (Neely et al., Cambridge)

No KPI ships without all of these. This is the schema of the KPI library:

```yaml
- id: cac_payback_months
  name: CAC Payback Period
  perspective: financial
  driver_parent: unit_economics
  type: lagging
  formula: "(sales_cost + marketing_cost) / (new_mrr * gross_margin_pct)"
  unit: months
  direction: lower_is_better
  frequency: monthly
  owner_role: CRO
  source_systems: [crm, gl]
  benchmark: { p25: 24, p50: 15, p75: 8, source: "SaaS benchmark cohort, ARR 5-20M" }
  target_rule: "min(current * 0.85, benchmark_p50)"
  alert_bands: { green: "<12", amber: "12-18", red: ">18" }
  applies_when: "business_model.revenue_model contains 'subscription'"
  pitfalls: "Excludes expansion revenue; distorted if headcount added ahead of demand."
```

`applies_when` is the selection engine. `benchmark` is what lets a survey-only user get a target
without having any history. `pitfalls` goes in the report appendix — it is a big credibility signal.

### 2.5 Layer 5 — Industry packs

Generic KPIs get you 40% of the way. The remaining 60% is sector-specific. Ship a pack per sector:

| Sector | Signature KPIs |
|---|---|
| SaaS / subscription | ARR, NRR/GRR, logo & revenue churn, CAC payback, Magic Number, Rule of 40, burn multiple, expansion %, ARR per FTE |
| E-commerce / D2C | Conversion rate, AOV, CAC/AOV ratio, contribution margin after ads, repeat rate, RFM cohorts, return rate, ROAS/MER |
| Retail (physical) | Sales/m², like-for-like growth, GMROI, sell-through, shrinkage, basket size, footfall conversion |
| Manufacturing | OEE (availability × performance × quality), first-pass yield, scrap %, OTIF, capacity utilization, changeover time, cost per unit |
| Professional services | Utilization, realization, bill rate, project margin, backlog/book-to-bill, revenue per consultant |
| Marketplace | GMV, take rate, liquidity (fill rate), supply/demand balance, cohort retention both sides |
| Logistics | Cost per km/shipment, on-time delivery, fleet utilization, empty-run %, damage rate |
| Hospitality / F&B | RevPAR, ADR, occupancy, covers, food & labour cost %, table turn |
| Healthcare | Bed occupancy, ALOS, readmission, cost per case, patient satisfaction |
| Construction | Cost performance index, schedule performance index (EVM), variation orders, safety TRIR |

**Sizing note:** a sector pack is ~25–40 fully-specified KPIs. That is roughly **1 focused day each**
with Claude drafting and you reviewing. This is the single largest content cost in the project and
it is unavoidable — it is also the moat.

### 2.6 Selection algorithm

```
candidates = generic_packs + industry_pack(profile) + model_packs(revenue_model, customer_type)
filter    → applies_when evaluates true against profile
filter    → data_availability: computable | estimable | not_possible   (never show not_possible)
score     → w1·intent_alignment + w2·perspective_gap + w3·leading_bonus
            + w4·audience_fit + w5·driver_tree_completeness − w6·redundancy(correlated metrics)
constrain → 1 North Star; 4–6 L1 exec KPIs; 12–20 L2; L3 unlimited but hidden behind drill-down
           → per-perspective min 2 / max 7
           → cap total L1+L2 at 20 if org_culture.kpi_experience == "low"
output    → kpi_set.json  (the contract for everything downstream)
```

---

## 3. Generating the data (Modes 1 & 2)

The credibility of a sample dashboard lives or dies here. Random numbers look random. The fix is a
**driver-based generative model with an accounting reconciliation pass.**

1. **Sample ~15 primitives** from benchmark distributions conditioned on sector/size/region
   (growth rate, margin, churn, seasonality amplitude, deal size dispersion...). Seeded RNG →
   the same profile always yields the same company. Reproducibility is a feature.
2. **Derive everything else through identities.** Revenue = customers × frequency × AOV.
   Headcount cost = FTE × sector wage index × region factor. Never sample two numbers that must agree.
3. **Add structure, not noise:** trend + seasonality (sector-specific) + weekday effects +
   promo/tender spikes + one or two deliberate anomalies (a churn spike, a margin dip) so the
   narrative engine has something real to find.
4. **Emit normalized fact tables**, not a flat blob:
   `transactions · customers · products · employees · budget_actuals · pipeline · inventory · gl_summary`
5. **Reconciliation gate.** Assertions run before anything renders: segment revenue sums to total,
   margins inside plausible sector bands, headcount cost/revenue sane, no negative inventory,
   cohort retention monotone. Fail → resample. This step is what separates "believable" from "fake".

Real uploaded data (Mode 3) enters the *same* fact-table contract via a mapping layer — so
everything downstream is identical whether the data is synthetic or real. That is the key to not
building the product twice.

---

## 4. Visualization & reporting

### 4.1 Chart specs, not chart code

Charts are declared as JSON, rendered by two backends from one spec:

```jsonc
{ "id":"rev_bridge_ytd", "type":"waterfall", "data":"facts.revenue_bridge",
  "x":"driver", "y":"delta", "title":"Revenue bridge vs plan, YTD",
  "annotate":["largest_negative"], "theme":"corporate" }
```

- **Web/interactive:** Plotly (or ECharts if the front end is JS).
- **Print:** the same spec → static SVG/PNG (kaleido) → embedded in PDF/DOCX/PPTX.

One spec, five outputs. Do not hand-write charts per export format — that is the trap that makes
these projects sprawl.

Chart-type discipline: trend→line, composition-over-time→stacked area, contribution→waterfall,
ranking→sorted bar, distribution→box/histogram, correlation→scatter with quadrants,
progress-to-target→bullet chart, cohort→heatmap. **No pie charts beyond 4 slices, no dual axes.**

### 4.2 Deliverable set (this is your "professional end product")

| # | Artifact | Format | Content |
|---|---|---|---|
| 1 | Interactive dashboard | HTML (self-contained) | Exec tab + one tab per function, filters, drill-down |
| 2 | Executive report | PDF | 1-page summary, scorecard, driver tree, 8–12 exhibits, insights, actions, methodology + assumptions appendix |
| 3 | Board deck | PPTX | 10–14 slides, one message per slide title |
| 4 | Editable report | DOCX / Google Doc | Same content, editable |
| 5 | Data workbook | XLSX | Raw facts, KPI calc sheets with live formulas, pivot-ready, definitions tab |
| 6 | Flat data | CSV bundle | One file per fact table |
| 7 | KPI definition catalogue | PDF/XLSX | The record sheets — owner, formula, source, target, pitfalls |
| 8 | Performance tracker | XLSX + dashboard tab | Monthly actual vs target vs prior year, RAG status, variance commentary slots |
| 9 | `profile.json` + `kpi_set.json` | JSON | Reproducibility + re-run inputs |

### 4.3 The report structure (McKinsey-style, standardized)

```
1  Executive summary — "So what?" in 5 bullets, each with a number
2  Scorecard — North Star + L1 KPIs, RAG vs target and vs benchmark
3  Diagnostic — where performance comes from (driver tree + waterfall)
4  Deep dives — one per function, each: chart → observation → implication
5  Benchmarks — you vs sector p25/p50/p75
6  Risks & watch-list — concentration, cash runway, leading-indicator alerts
7  Recommended actions — prioritized by impact × effort, each tied to a KPI and an owner
8  Appendix — KPI definitions, data sources, assumptions & provenance, methodology
```

Insight generation is a **hybrid**: deterministic detectors first (variance vs target/prior/benchmark,
trend break, outlier segment, correlation shift, concentration risk, runway) produce a *facts table*
of candidate findings with the numbers already computed; the LLM only ranks, merges and writes them
in prose. It cannot invent a finding, because it only ever sees the detector output.

---

## 5. The AI layer (Mode 3) — multi-agent design

Built on the **Claude Agent SDK**. Your instinct about a planner + a technical agent is right; the
refinement is that the executor should mostly *not* be an LLM.

| Agent | Model | Job | Output |
|---|---|---|---|
| **Intake** | Sonnet | Conversational; asks multiple-choice questions with a free-text escape; inspects uploaded file headers/samples only | validated `CompanyProfile` (via tool call, schema-enforced) |
| **Mapper** | Sonnet | Maps uploaded columns → canonical fact-table fields; proposes transforms; flags unmappable | `mapping.json` + confidence per field |
| **Planner** | Opus | Profile + intent → which KPI packs, which report sections, which exhibits, what to emphasize | `plan.json`, validated against schema |
| **Executor** | *none — code* | Runs the deterministic pipeline against the plan | all artifacts |
| **Custom transform** | Sonnet | Only when the plan needs something outside the library; writes a sandboxed pandas function | reviewed code, run in restricted subprocess |
| **Narrator** | Opus | Facts table → prose (summary, observations, recommendations) | markdown sections |
| **Critic** | Sonnet | Every number in the prose must appear in the facts table; checks BSC coverage, leading/lagging mix, unsupported causal claims | pass/fail + fixes |

**Token economics.** The model never touches row-level data. Intake ≈ 10–20K tokens; Planner ≈ 15K;
Narrator ≈ 20K; Critic ≈ 15K. A full AI Builder run lands around **60–120K tokens ≈ well under $1**
at Sonnet-heavy mix. Modes 1 and 2 cost **zero tokens** — they are pure code. That is the right
place for the free tier.

**Failure handling:** schema validation on every agent boundary, one retry with the validation error
fed back, then fall back to the deterministic default for that field. The pipeline must always
produce a deliverable, even if degraded — a partially-defaulted report beats an error page.

---

## 6. Stack recommendation

**Core engine: Python.** Not a close call — pandas/numpy for the data, Plotly for charts,
openpyxl/python-docx/python-pptx/WeasyPrint for exports, and the Claude Agent SDK has first-class
Python support. Nothing in the JS ecosystem covers XLSX-with-formulas + DOCX + PPTX + PDF as well.

**Front end:** Next.js + React for the real product; Streamlit for the first two weeks so you are not
blocked on UI while the engine is unproven. The engine is a library with a CLI — the UI is a skin,
and swapping Streamlit → Next.js later costs days, not weeks, *provided* the engine never imports
the UI framework.

```
kpi_maker/
  profile/       schema (pydantic), validation, benchmark priors, provenance
  survey/        question graph, branching, defaults engine
  kpi/           library/*.yaml (generic + per-sector), selection engine, driver trees
  datagen/       primitives, derivation, seasonality, anomalies, reconciliation
  ingest/        upload → canonical fact tables, mapping, profiling, quality report
  metrics/       KPI computation, targets, benchmarks, RAG status
  insight/       deterministic detectors → facts table
  viz/           chart specs → plotly | static
  render/        dashboard.html, pdf, xlsx, docx, pptx, csv
  agents/        intake, mapper, planner, narrator, critic (Claude Agent SDK)
  cli.py         kpi-maker run --profile x.json --out ./out
  api/           FastAPI (thin wrapper over cli)
ui/              Next.js (phase 3)
```

**Env:** Python 3.11+ (3.9 is EOL-adjacent and blocks newer libs), Node 20+ if/when the JS UI lands.
Both current installs need upgrading.

---

## 7. Effort estimate — honest

| Milestone | Scope | Time (solo, Claude-assisted) |
|---|---|---|
| **M0 — Vertical slice** | 1 sector, hardcoded profile, 12 KPIs, synthetic data, HTML dashboard + PDF | **2–3 days** |
| **M1 — Mode 1 (Samples)** | 3 polished sample companies, all 9 deliverables | +1 week |
| **M2 — Mode 2 (Survey)** | Question graph, benchmark defaults, 5 sector packs, "Surprise me" | +2 weeks |
| **M3 — Mode 3 (AI Builder)** | Agent pipeline, file upload, column mapping, narrative + critic | +2–3 weeks |
| **M4 — Product** | Next.js UI, auth, run history, sharing, Google Docs/Sheets export | +3–4 weeks |
| **Ongoing** | Sector packs, ~1 day each | 10 sectors ≈ 2 weeks |

**≈ 6–10 weeks to a genuinely sellable product.** Two weeks to something that already impresses people.

### What is actually hard (and where estimates break)

1. **Mapping arbitrary uploaded data** to the KPI model. Everyone's export is different. Mitigate by
   supporting a narrow set of shapes well (transaction log, P&L export, CRM deal export) and being
   explicit about the rest rather than half-working on everything.
2. **Credible synthetic data.** The reconciliation gate is the answer; budget real time for it.
3. **Narrative that doesn't hallucinate.** Solved structurally (detectors + critic), but the critic
   needs tuning against real outputs.
4. **Benchmark data.** The commercial ones are licensed and cannot be scraped or embedded. Start with
   public sources (Eurostat, TÜİK, OECD, SEC filings, Damodaran's public margin/ROIC datasets by
   industry, published sector surveys) and **cite every benchmark inline**. Uncited benchmarks are
   the fastest way to lose a business customer's trust.
5. **Localization.** TR/EU vs US differ in fiscal calendar, VAT/KDV, chart of accounts, and which
   benchmarks apply. Design for it from day one; retrofitting is painful.

---

## 8. Recommended build order

1. `CompanyProfile` schema + validation. Everything hangs off this. **Do not skip ahead.**
2. KPI library format + 15 generic KPIs + the selection engine.
3. Data generator + reconciliation gate for one sector.
4. Metrics engine → facts table.
5. Chart specs → HTML dashboard.
6. PDF + XLSX exports.
7. → **M0 done. Look at the output. Everything after this is informed by that first artifact.**
8. Survey engine + benchmark defaults.
9. Sector packs, one at a time.
10. Agents last — they are the easiest layer once the deterministic core exists, and the hardest to
    debug if it doesn't.
```

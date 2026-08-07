# Execution roadmap

How to get from the working M0 slice to the product described in
[ARCHITECTURE.md](ARCHITECTURE.md). Ordered by dependency, not by excitement.

**Sequencing principle:** every milestone must end with something you can open
and look at. No milestone is "infrastructure only" — if it doesn't change an
artifact, it's in the wrong place in the list.

**The one ordering rule that matters:** the AI layer comes LAST. It is the
easiest layer to add once the deterministic core exists and the hardest thing in
the world to debug if the core is still moving. Everything Claude does in Mode 3
(fill a profile, pick a plan, write prose) is a thin wrapper over machinery that
must already work without it.

---

## Where we are

| | Status |
|---|---|
| **M0 — SaaS vertical slice** | **Done.** 24 KPIs, 12 reconciliation checks, 9 charts, dashboard + workbook + CSV |

Foundation now in place and reusable by everything below: `CompanyProfile`
schema with cross-block validation · KPI record-sheet format + sandboxed
selection engine · fact-table contract · metrics registry · deterministic
detectors · validated chart palette · dashboard/workbook renderers.

---

## Effort summary (solo, Claude-assisted)

| Scope | Time |
|---|---|
| **Sellable core** — Modes 1+2+3, Streamlit UI, 3 sectors, full report set | **7–9 weeks** |
| **Full product as described** — 10 sectors, Next.js, Google export, multi-tenant | **14–18 weeks** |

The gap between those two numbers is almost entirely **sector content (M2)**,
**a real UI (M7)** and **productionisation (M9)** — not core engineering. Ship
the 7–9 week version, put it in front of people, and let their reaction decide
how much of the rest is worth building.

---

# Phase 1 — Complete the deliverable set

## M1 · Report engine (PDF / PPTX / DOCX)
**3–4 days · no dependencies · do this next**

Right now the pipeline produces a dashboard and a workbook. The consulting
deliverable — the thing that gets emailed to a board — is still missing.

- [ ] `viz/export.py` — chart specs → static SVG/PNG via `kaleido`. Same specs,
      no second chart implementation.
- [ ] `render/report.py` — Jinja2 → print-CSS HTML → PDF.
      **Windows note:** WeasyPrint needs GTK and is painful here. Use headless
      Chromium (`playwright`) for print-to-PDF, or `fpdf2` if you want zero
      browser dependency. Decide once; it's a one-line swap either way.
- [ ] `render/deck.py` — `python-pptx`, 10–14 slides, **one message per slide
      title** ("Churn is concentrated in SMB", not "Churn analysis").
- [ ] `render/doc.py` — `python-docx`, same content, editable.
- [ ] Report structure fixed at the 8 sections in ARCHITECTURE §4.3.

**Acceptance:** `run` emits all 9 artifacts. The PDF is something you would
actually send to a CFO without editing it first.

## M2 · Sector breadth
**~3.5 weeks · biggest single cost in the project · this is the moat**

Two separable pieces. Do one archetype + one pack first and time yourself before
committing to the whole list.

**2a. Generator archetypes (~2 days each).** Most sectors share economics, so
five generators cover ten-plus sectors:

| Archetype | Covers |
|---|---|
| `subscription` (done) | SaaS, memberships, telco |
| `transactional` | e-commerce, D2C, retail |
| `project` | agencies, professional services, construction |
| `production` | manufacturing, food production |
| `marketplace` | platforms, two-sided networks |

**2b. KPI packs (~1 day each).** 25–40 fully-specified record sheets per sector.
Priority order: e-commerce → professional services → manufacturing → retail →
marketplace → logistics → hospitality → healthcare → construction.

- [ ] Extract the shared simulation core out of `datagen/saas.py` (calibration
      loop, seasonality, anomaly planting and the reconciliation gate are all
      sector-agnostic already).
- [ ] `GENERATORS` registry keyed on business model, `SECTOR_TO_ARCHETYPE` map.
- [ ] Per-sector reconciliation identities (manufacturing needs units × price =
      revenue and a capacity ceiling; services needs hours × rate = revenue).

**Acceptance:** a profile in any covered sector runs end to end and its
reconciliation checks are sector-specific, not generic.

**Also fix here:** the SaaS pack is at 21% leading indicators against a 30%
target. Add product-usage, engagement and capacity metrics. Every new pack
should hit 30% at authoring time — the selection engine already warns when it
doesn't.

---

# Phase 2 — Modes 1 and 2 (zero-token product)

## M3 · The question framework
**1 week · the piece you specifically asked to get academically right**

This is a research-and-design task before it is a coding task. Three separate
artifacts.

**3a. The diagnostic frame — what we must understand about a business.**
Draw the input model from established frameworks rather than inventing one:

| Frame | What it contributes to the profile |
|---|---|
| **Business Model Canvas** (Osterwalder) | value proposition, segments, channels, revenue & cost structure |
| **Porter value chain** | which operations exist → which process KPIs are even possible |
| **McKinsey 7S** | the `org_culture` block — strategy, structure, systems, staff, skills, style, shared values |
| **Balanced Scorecard** (Kaplan & Norton) | already implemented — forces KPI coverage |
| **OGSM / OKR** | intent, horizon and target-setting |
| **Greiner growth model** | maps company stage → which problems are structurally likely |

**3b. The question bank.** Design rules, from survey methodology:

- **14 core questions maximum** before the user sees a result. Everything else
  is an optional deep-dive that visibly *unlocks more precise KPIs*.
- **Every question must branch something.** If two answers produce the same
  dashboard, delete the question. Enforce with a test that asserts every
  question id is referenced by at least one `applies_when` or prior.
- **Bands, never free-text numbers.** People answer bands honestly and abandon
  open number fields. (This also avoids the satisficing problem — a respondent
  who can't recall an exact figure invents one.)
- **Ask intent before facts.** Objective is the single strongest predictor of
  which KPIs matter, and it primes better answers to everything after.
- **Every question has "I don't know / use sector average."** Fills from the
  prior, tags provenance, and the report footnotes it. No dead ends.
- **Never ask what we can derive.** Headcount by function can be estimated from
  total headcount + sector; ask only if the user wants precision.

- [ ] `survey/questions.yaml` — question graph: id, text, type, options,
      `unlocks`, `fills` (dotted profile path), `show_when`.
- [ ] `survey/engine.py` — branching, progress, partial-profile assembly.
- [ ] Test: every profile field required by any generator is reachable.

**3c. The two taxonomies.**

- **Sector taxonomy.** NACE (EU/TR) and NAICS (US) are the defensible public
  backbones. Map both onto ~15 `internal_sector` values — the user picks from a
  searchable list, we store the official code for credibility.
- **Intent taxonomy.** Already 10 objectives in the schema. Validate against real
  consulting engagement types; each objective must map to a KPI weighting and a
  report emphasis, or it isn't a real category.

**Acceptance:** a non-technical person completes the survey in under 4 minutes
and the resulting `profile.json` passes validation.

## M4 · Benchmark priors & defaults engine
**4–5 days · gates Mode 2 credibility**

- [ ] `profile/benchmarks.py` — priors keyed on (sector, size band, region),
      returning a distribution not a point estimate.
- [ ] Public sources only: Eurostat, TÜİK, OECD, SEC filings, **Damodaran's
      industry datasets** (margins, ROIC, cost of capital — free and citable).
- [ ] Every prior carries a `source` string that renders into the appendix.
- [ ] **"Surprise me"** — sample a complete, self-consistent profile from the
      priors. This is nearly free once priors exist and is the best demo you have.
- [ ] Replace the illustrative SaaS placeholder benchmarks.

> **Commercial blocker, flagged again:** licensed benchmark reports cannot be
> scraped or embedded. Either stay on public sources with citations, buy a
> licence, or aggregate your own customer data once you have customers. Decide
> before selling, not after.

## M5 · Modes 1 & 2 wired up
**2–3 days**

- [ ] 5–6 curated sample profiles across sectors, each with a deliberate story
      (a churn problem, a cash problem, a margin problem).
- [ ] `samples/gallery.json` — descriptions and thumbnails for the menu.
- [ ] Survey → profile → existing pipeline. **No new pipeline code should be
      needed here.** If it is, the profile contract has leaked.

**Acceptance:** Modes 1 and 2 work end to end and cost zero tokens.

---

# Phase 3 — Real data and the AI layer

## M6 · Ingestion
**1.5–2 weeks · the genuinely hard one · prerequisite for Mode 3**

Everyone's export is different. The honest strategy is to support a **narrow set
of shapes really well** and be explicit about the rest, rather than half-working
on everything.

- [ ] Parsers: CSV/XLSX/Google Sheets export, multi-sheet, messy headers.
- [ ] `ingest/profiler.py` — column type inference, cardinality, date detection,
      currency detection, null and duplicate analysis.
- [ ] `ingest/mapping.py` — uploaded columns → canonical fact-table fields, with
      a **confidence score per field**. Deterministic matching first (name
      similarity, type compatibility, value distribution); Claude only for what's
      left over.
- [ ] Supported shapes v1: transaction log · P&L export · CRM deal export ·
      subscription/billing export · headcount roster.
- [ ] `ingest/quality.py` — a data quality report the user sees BEFORE any
      dashboard: what mapped, what didn't, what we're assuming.
- [ ] Real data enters the **same fact-table contract** as synthetic. Everything
      downstream must be identical. This is what stops you building the product twice.

**Acceptance:** upload a messy real spreadsheet, get an honest mapping report,
and the same nine artifacts.

## M7 · AI Builder (Mode 3)
**2 weeks · built on the Claude Agent SDK**

Your instinct — a planning AI plus a technical AI — is right. The refinement:
**the executor should not be an LLM.**

| Agent | Model | Output | Guard |
|---|---|---|---|
| **Intake** | Sonnet | `CompanyProfile` | schema-enforced tool call |
| **Mapper** | Sonnet | `mapping.json` + confidence | deterministic matcher runs first |
| **Planner** | Opus | `plan.json` — packs, sections, exhibits | validated against schema |
| **Executor** | *code, not a model* | all artifacts | — |
| **Transform** | Sonnet | sandboxed pandas fn, only when the plan needs something the library lacks | restricted subprocess, no network/fs |
| **Narrator** | Opus | prose sections | sees ONLY the facts table |
| **Critic** | Sonnet | pass/fail + fixes | every number in the prose must appear in the facts table |

- [ ] `agents/` — one module per agent, schema at every boundary.
- [ ] **Failure policy:** validate → one retry with the error fed back → fall
      back to the deterministic default. The pipeline must ALWAYS produce a
      deliverable. A partially-defaulted report beats an error page.
- [ ] Token metering per run, surfaced to the user before they commit.
- [ ] Conversation UI: multiple-choice questions with a free-text escape at the
      end, exactly as you described.

**Economics:** the model never touches row-level data. Intake ~15K, Planner ~15K,
Narrator ~20K, Critic ~15K → **~60–120K tokens, well under $1/run.** Modes 1 and
2 stay at zero. That is the right shape for a free tier.

---

# Phase 4 — Product

## M8 · UI
**Streamlit 3–4 days → Next.js 2–3 weeks**

- [ ] Streamlit first: main menu (3 modes), survey form, run progress, artifact
      downloads, embedded dashboard. Ugly but real, and it unblocks user testing.
- [ ] Next.js + FastAPI when the engine stops changing. The engine must never
      import a UI framework — that constraint is what makes this swap cost days.

## M9 · Google Docs / Sheets export
**3 days · OAuth is the annoying part, not the API**

## M10 · Productionisation
**2–3 weeks** — auth, run history, shareable links, storage, billing/token
metering, rate limits.

---

# What "done" means — the deliverable spec

You asked for this to be clarified. Standard output of every run, every mode:

| # | Artifact | Format | Status |
|---|---|---|---|
| 1 | Interactive dashboard | HTML | **done** |
| 2 | Executive report | PDF | M1 |
| 3 | Board deck | PPTX | M1 |
| 4 | Editable report | DOCX / Google Doc | M1 / M9 |
| 5 | Data workbook | XLSX | **done** |
| 6 | Flat data | CSV bundle | **done** |
| 7 | KPI definition catalogue | XLSX tab + PDF | **done** / M1 |
| 8 | Performance tracker | XLSX, live formulas | **done** |
| 9 | Reproducibility inputs | `profile.json`, `kpi_set.json` | **done** |

Report structure (fixed, all sectors): exec summary → scorecard → diagnostic
(driver tree + waterfall) → functional deep dives → benchmarks → risks &
watch-list → prioritised actions → appendix (definitions, assumptions,
provenance, methodology).

---

# Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| **Benchmark licensing** | **Blocks commercial launch** | Public sources + citations now; licence or self-aggregate later. Decide before selling. |
| Ingestion breadth (M6) | High — classic estimate-killer | Support 5 shapes well; explicit failure for the rest |
| Sector content cost (M2) | High — 3.5 weeks of authoring | Archetypes cut generator work 2×; packs are parallelisable |
| Narrative hallucination | Medium | Structurally solved (detectors + critic); needs tuning against real output |
| Synthetic data credibility | Medium | Reconciliation gate exists and already caught two real bugs |
| Localisation (TR/EU vs US) | Medium | Fiscal calendar, VAT/KDV, chart of accounts differ. Design in from day one — retrofitting is painful |
| Scope drift into "BI tool" | Medium | This is a *generator*, not Tableau. Say no to live data connections |

---

# Recommended order

```
M1 Reports ─────────► completes the deliverable set, unblocks demos
M3 Questions ───────► can run in parallel; it's research before code
M4 Benchmarks ──────► gates Mode 2 credibility
M2 Sectors ─────────► the long pole; start after one archetype is proven
M5 Modes 1&2 ───────► first genuinely usable product
M8 Streamlit ───────► put it in front of people HERE
   ↓ (learn from real users before building more)
M6 Ingestion ───────► hard; only worth it if users ask for it
M7 AI Builder ──────► easy once M6 is solid
M8 Next.js · M9 Google · M10 Production
```

**The checkpoint that matters is after M5+Streamlit.** That is the first moment
real users can react. Everything after it should be shaped by what they say, not
by this document.

"""Tests for sector breadth: the archetype registry, the contract, e-commerce.

P4's risk is a quiet one. Every layer below the generator was built to degrade
gracefully on a missing table — identities skip, unknown schemas are ignored,
charts omit themselves, KPIs drop with a recorded reason. That is right for a
partial upload and wrong for an archetype that legitimately has no
`mrr_movements`: a new sector can *appear* to work while checking nothing.

So the most important test here is the vacuity guard, for the same reason the
tier split was the most important test in P2. The rest establish that the
second archetype is a real one rather than subscription with the labels
changed.

Six groups:

  1. **The registry** — archetypes declare themselves; an unknown one is refused.
  2. **The vacuity guard** — a generated sector with no identities fails.
  3. **The contract, per archetype** — schemas and identities apply to the
     right sector and not to the wrong one.
  4. **The e-commerce generator** — its own identities hold, and a broken
     fixture is refused for both sources.
  5. **The pack** — every formula parses, resolves and evaluates.
  6. **The knobs** — seasonality, volatility and anomalies measurably move
     the output, and their defaults do not.

    python -m tests.sector
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kpi_maker.datagen as datagen
from kpi_maker.cli import load_profile
from kpi_maker.contract import (ReconciliationError, UncheckedArchetype,
                                run_gate, schemas_for, validate_schemas)
from kpi_maker.contract.identities import CHECKS, Tier
from kpi_maker.contract.schemas import (ECOMMERCE_SCHEMAS, FACT_SCHEMAS,
                                        SCHEMAS_BY_ARCHETYPE, UNIVERSAL_SCHEMAS)
from kpi_maker.datagen.base import GENERATORS, apply_amplitude, volatile
from kpi_maker.kpi.schema import KPISet
from kpi_maker.kpi.selection import load_library, select
from kpi_maker.metrics.engine import compute
from kpi_maker.spec.schema import GeneratorParams
from kpi_maker.viz.charts import build_all, default_exhibits

ROOT = Path(__file__).resolve().parents[1]
SAAS = ROOT / "samples" / "northwind_saas.json"
RETAIL = ROOT / "samples" / "kestrel_retail.json"

_failures: List[str] = []
_checks = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    if not ok:
        _failures.append(f"{name}: {detail}")
        print(f"  FAIL   {name}" + (f"  — {detail}" if detail else ""))


# --------------------------------------------------------------------------
# 0. Every declared sector runs
# --------------------------------------------------------------------------

def test_every_sector_runs() -> None:
    """No sector the schema offers may raise.

    `BusinessModel` declares ten sectors; two have their own archetype and pack.
    For a long time the other eight raised `FileNotFoundError: KPI pack not
    found: retail` out of the selection engine, or `ValueError: No data
    generator for business model 'retail'` out of the source stage — so a survey
    respondent who picked "manufacturing" got a stack trace rather than a
    dashboard.

    The bar here is not that every sector is *good*. It is that every sector
    resolves to something runnable and that an approximated one says so. A
    sector that quietly borrowed another's content would pass a "does it run"
    test and fail the user, so the warning is asserted as hard as the run.
    """
    print("\nevery declared sector runs")

    from kpi_maker.profile import sectors
    from kpi_maker.profile.schema import BusinessModel, CompanyProfile
    from kpi_maker.spec.schema import RunSpec

    declared = [m.value for m in BusinessModel]
    check("the schema still declares ten sectors", len(declared) == 10,
          f"{len(declared)}: {declared}")

    base = json.loads(SAAS.read_text(encoding="utf-8"))
    exact = set(sectors.supported_sectors())

    for model in declared:
        raw = json.loads(json.dumps(base))
        raw["business_model"]["type"] = model
        profile = CompanyProfile(**raw)
        spec = RunSpec.for_profile(profile)

        # Both halves must resolve to something the registries actually hold.
        archetype = spec.resolve_archetype()
        check(f"{model}: archetype {archetype!r} is registered",
              archetype in GENERATORS, str(sorted(GENERATORS)))

        try:
            kpi_set = select(profile)
        except Exception as exc:                            # noqa: BLE001
            check(f"{model}: selection does not raise", False,
                  f"{type(exc).__name__}: {exc}")
            continue

        check(f"{model}: the scorecard is not empty", len(kpi_set.kpis) >= 8,
              f"{len(kpi_set.kpis)} KPIs")
        check(f"{model}: the north star is a selected KPI",
              kpi_set.by_id(kpi_set.north_star) is not None,
              kpi_set.north_star)

        # Balanced Scorecard coverage has to survive the fallback too, or the
        # degraded run is a financial dump rather than a scorecard.
        perspectives = {k.perspective for k in kpi_set.kpis}
        check(f"{model}: all four perspectives are represented",
              len(perspectives) == 4, str(sorted(p.value for p in perspectives)))

        warned = "_sector_warning" in kpi_set.rationale
        if model in exact:
            check(f"{model}: an exact sector carries no approximation warning",
                  not warned and spec.archetype_note() is None)
        else:
            check(f"{model}: an approximated pack says so", warned)
            check(f"{model}: an approximated archetype says so",
                  spec.archetype_note() is not None)
            check(f"{model}: the warning names the sector",
                  model in kpi_set.rationale.get("_sector_warning", ""))


# --------------------------------------------------------------------------
# 1. The registry
# --------------------------------------------------------------------------

def test_registry() -> None:
    print("\nregistry")

    available = datagen.available()
    check("both archetypes are registered",
          set(available) >= {"saas", "ecommerce"}, str(available))
    check("registration is by importing the package, not by editing a dict",
          all(callable(GENERATORS[a]) for a in available))

    # Every generator takes the same two arguments, so the pipeline never has
    # to know which sector it is running.
    import inspect
    for archetype, fn in GENERATORS.items():
        params = list(inspect.signature(fn).parameters)
        check(f"{archetype} takes (profile, params)",
              params[:2] == ["profile", "params"], str(params))

    from kpi_maker.pipeline.stages import GENERATORS as pipeline_view
    check("the pipeline sees the same registry", pipeline_view is GENERATORS)

    # The compatibility shim two callers still import from.
    from kpi_maker.datagen.saas import calibration_tolerance, generate, reconcile
    check("the saas shim still exports what its callers import",
          callable(generate) and callable(reconcile)
          and calibration_tolerance(100) > 0)


# --------------------------------------------------------------------------
# 2. The vacuity guard — the most important test here
# --------------------------------------------------------------------------

def test_vacuity_guard(saas_data) -> None:
    print("\nvacuity guard")

    profile = load_profile(SAAS)

    # A generated archetype nobody wrote structural identities for must fail,
    # even though the four universal P&L checks pass. Letting those satisfy the
    # guard would wave a sector through on arithmetic that says nothing about
    # what it sells.
    try:
        run_gate(saas_data.tables, profile, source="synthetic",
                 archetype="a_sector_nobody_wrote_checks_for")
        check("an unchecked generated archetype is refused", False, "it passed")
    except UncheckedArchetype as exc:
        check("an unchecked generated archetype is refused", True)
        check("and the error says how to fix it",
              "identities.py" in str(exc) and "archetypes=" in str(exc))

    check("UncheckedArchetype is a ReconciliationError",
          issubclass(UncheckedArchetype, ReconciliationError),
          "callers already catching the gate's error must catch this too")

    # Uploads are exempt: partial coverage is normal there, and P2's design is
    # to report what could not be checked rather than refuse to render.
    result = run_gate(saas_data.tables, profile, source="upload",
                      archetype="a_sector_nobody_wrote_checks_for")
    check("an upload is never refused for the same reason",
          result.passed and len(result.checks) >= 1)

    # A real archetype passes because it has real coverage.
    for archetype in ("saas",):
        ran = run_gate(saas_data.tables, profile, source="synthetic",
                       archetype=archetype)
        check(f"{archetype} has genuine structural coverage",
              len(ran.checks) > 4,
              f"{len(ran.checks)} checks — more than the four universal ones")


# --------------------------------------------------------------------------
# 3. The contract, per archetype
# --------------------------------------------------------------------------

def test_contract_scoping(saas_data, retail_data) -> None:
    print("\ncontract scoping")

    universal = [c for c in CHECKS if c.archetypes is None]
    check("the universal identities are P&L arithmetic only",
          len(universal) == 4, str([c.name for c in universal]))
    for name in ("saas", "ecommerce"):
        owned = [c for c in CHECKS if c.archetypes and name in c.archetypes]
        structural = [c for c in owned if c.tier is Tier.structural]
        check(f"{name} declares structural identities of its own",
              len(structural) >= 5, f"{len(structural)}")

    # A subscription identity must not be applied to a retailer, and vice
    # versa. `requires` alone cannot express that — it only says a table is
    # absent, not that the sector has no such concept.
    arr_check = next(c for c in CHECKS if c.name == "arr = mrr x 12")
    check("a subscription identity does not apply to e-commerce",
          not arr_check.applies_to("ecommerce"))
    check("but does to saas", arr_check.applies_to("saas"))
    check("and applies when the archetype is unknown",
          arr_check.applies_to(None),
          "an unclassified upload should still be held to everything it can be")

    # Schemas split the same way.
    check("the universal financials does not demand mrr",
          "mrr" not in UNIVERSAL_SCHEMAS["monthly_financials"].columns)
    check("the subscription one does",
          "mrr" in schemas_for("saas")["monthly_financials"].columns)
    check("e-commerce gets its own tables",
          {"orders", "traffic", "inventory", "buyers"}
          <= set(schemas_for("ecommerce")))
    check("and not the subscription ones",
          not ({"mrr_movements", "pipeline", "product_usage"}
               & set(schemas_for("ecommerce"))))
    check("an unknown archetype falls back to the union",
          schemas_for("nope") is FACT_SCHEMAS)

    # `customers` means different things in the two sectors, which is exactly
    # why the lookup is per-archetype rather than one flat dict.
    check("the two customers tables genuinely differ",
          set(schemas_for("saas")["customers"].columns)
          != set(ECOMMERCE_SCHEMAS["customers"].columns))

    for archetype, data in (("saas", saas_data), ("ecommerce", retail_data)):
        passed, problems = validate_schemas(data.tables, archetype=archetype)
        check(f"{archetype} output validates against its own schemas",
              not problems, str(problems))

    # The retailer's data held to the subscription contract must fail, or the
    # per-archetype split is not doing anything.
    _, problems = validate_schemas(
        {"monthly_financials": retail_data.tables["monthly_financials"]},
        archetype="saas")
    check("retail financials fail the subscription contract", bool(problems),
          "if this passes, the mrr/arr split is not being enforced")


# --------------------------------------------------------------------------
# 4. The e-commerce generator
# --------------------------------------------------------------------------

def test_ecommerce(retail_data) -> None:
    print("\ne-commerce generator")

    tables = retail_data.tables
    profile = load_profile(RETAIL)

    check("it emits the tables its schemas declare",
          {"orders", "traffic", "inventory", "buyers", "customers",
           "monthly_financials", "headcount", "marketing"} <= set(tables))
    check("and none of the subscription ones",
          not ({"mrr_movements", "pipeline", "product_usage", "sales_capacity"}
               & set(tables)))
    check("its own identities all ran and passed",
          len(retail_data.checks) >= 14, f"{len(retail_data.checks)}")

    orders = tables["orders"]
    net = orders["gross_revenue"] - orders["discounts"] - orders["returns"]
    check("net revenue is never negative", (net >= -1e-6).all())

    fin = tables["monthly_financials"]
    by_month = orders.assign(net=net).groupby("month")["net"].sum()
    check("orders tie to the P&L",
          np.allclose(by_month.reindex(fin["month"]).to_numpy(),
                      fin["revenue"].to_numpy(), rtol=1e-6))

    traffic = tables["traffic"]
    check("the funnel is ordered",
          (traffic["checkouts"] <= traffic["add_to_carts"] + 1e-6).all()
          and (traffic["add_to_carts"] <= traffic["sessions"] + 1e-6).all())

    buyers = tables["buyers"]
    check("the buyer split adds up",
          np.allclose(buyers["new_buyers"] + buyers["repeat_buyers"],
                      buyers["active_buyers"]))

    # The clamps in _build_traffic never bind on real output — sessions run
    # 14-62 per order and checkouts about 1.3 — so a mutation test removing
    # them changed nothing. They are insurance against a future
    # SESSIONS_PER_ORDER near 1, and insurance that is never exercised is
    # indistinguishable from dead code, so it is exercised here directly.
    from kpi_maker.datagen.ecommerce import _build_traffic
    starved = pd.DataFrame({
        "month": list(tables["orders"]["month"].unique()[:3]) * 1,
        "channel": ["organic", "email", "marketplace"],
        "orders": [100.0, 100.0, 100.0],
        "sessions": [100.0, 60.0, 10.0],      # fewer sessions than orders
    })
    clamped = _build_traffic(starved, None, np.random.default_rng(1))
    check("the funnel clamp holds when sessions are scarce",
          (clamped["checkouts"] <= clamped["sessions"] + 1e-9).all()
          and (clamped["add_to_carts"] <= clamped["sessions"] + 1e-9).all(),
          clamped.to_string())

    # Calibration, to the same standard the subscription generator is held to.
    trailing = float(by_month.sort_index().iloc[-12:].sum())
    drift = abs(trailing - profile.financials.revenue) / profile.financials.revenue
    check("it hits the stated revenue", drift < 0.02, f"{drift:.2%} drift")
    active = int(tables["customers"]["is_active"].sum())
    count_drift = abs(active - profile.market.customer_count) / profile.market.customer_count
    check("and the stated buyer count", count_drift < 0.20,
          f"{active:,} vs {profile.market.customer_count:,} ({count_drift:.1%})")

    # Broken data must be refused for BOTH sources — a structural violation is
    # not a matter of who supplied it.
    broken = dict(tables)
    bad_traffic = tables["traffic"].copy()
    bad_traffic.loc[0, "checkouts"] = bad_traffic.loc[0, "sessions"] * 3
    broken["traffic"] = bad_traffic
    for source in ("synthetic", "upload"):
        try:
            run_gate(broken, profile, source=source, archetype="ecommerce")
            check(f"a broken funnel is refused for {source}", False, "it passed")
        except ReconciliationError as exc:
            check(f"a broken funnel is refused for {source}", True)
            check(f"and the message names the failure ({source})",
                  "checkouts" in str(exc))

    # A calibration miss, by contrast, is fatal only for generated data. It is
    # induced by moving the PROFILE rather than the tables: scaling a money
    # column on its own breaks a structural identity too — discounts end up
    # exceeding gross — and then the fixture is testing the wrong tier. Moving
    # the profile is also the real scenario, where a user's stated revenue
    # disagrees with the file they uploaded.
    overstated = profile.model_copy(deep=True)
    overstated.financials.revenue = profile.financials.revenue * 3
    try:
        run_gate(tables, overstated, source="synthetic", archetype="ecommerce")
        check("a calibration miss is fatal for generated data", False, "it passed")
    except ReconciliationError as exc:
        check("a calibration miss is fatal for generated data", True)
        check("and it is the revenue identity that fires",
              "ending revenue" in str(exc), str(exc)[:90])
    reported = run_gate(tables, overstated, source="upload", archetype="ecommerce")
    check("but only a warning for an upload", bool(reported.warnings),
          "the data is the user's; the profile is the guess")
    check("and the run still produces its check list",
          len(reported.checks) >= 10)


# --------------------------------------------------------------------------
# 5. The pack
# --------------------------------------------------------------------------

def test_pack(retail_data) -> None:
    print("\ne-commerce pack")

    profile = load_profile(RETAIL)
    library = load_library(["ecommerce"])
    check("the pack loads", len(library) >= 15, f"{len(library)} KPIs")

    formulas = [k for k in library if k.compute.kind.value == "formula"]
    check("every KPI in it is a formula", len(formulas) == len(library),
          f"{len(library) - len(formulas)} builtins — the point of the pack is "
          f"that a sector needs no new Python")

    # Benchmarks must not be the SaaS ones. A retailer told its 42% gross
    # margin is bottom-quartile against a software p25 of 65% is being
    # misinformed by its own scorecard.
    saas = {k.id: k for k in load_library(["saas"])}
    shared = [k for k in library if k.id in saas and k.benchmark and saas[k.id].benchmark]
    check("shared ids carry their own benchmarks",
          all(k.benchmark.p50 != saas[k.id].benchmark.p50 for k in shared),
          str([k.id for k in shared
               if k.benchmark.p50 == saas[k.id].benchmark.p50]))
    check("and there is at least one such id to check",
          len(shared) >= 1, "otherwise the assertion above is vacuous")

    # Every formula must evaluate against the real tables.
    kset = KPISet(kpis=library, north_star=library[0].id)
    results = compute(kset, retail_data.tables, profile)
    failed = [(r.kpi.id, r.reason) for r in results if not r.computed]
    check("every formula computes against the generated tables",
          not failed, str(failed))

    values = {r.kpi.id: r for r in results if r.computed}
    for kid, low, high in (("conversion_rate", 0.0, 0.25),
                           ("return_rate", 0.0, 0.6),
                           ("discount_rate", 0.0, 0.6),
                           ("repeat_purchase_rate", 0.0, 1.0)):
        r = values.get(kid)
        current = None if r is None else r.current
        check(f"{kid} is a plausible fraction",
              current is not None and low <= current <= high, str(current))

    # Selection has to work end to end for the sector, which is what failed
    # before this phase: `KPI pack not found: ecommerce`.
    chosen = select(profile)
    check("selection produces a scorecard", len(chosen.kpis) >= 12,
          f"{len(chosen.kpis)}")
    check("with a sector-appropriate north star",
          chosen.north_star in {k.id for k in library}, chosen.north_star)


def test_exhibits(retail_data, saas_data) -> None:
    print("\nexhibits")

    registered = default_exhibits()
    check("the retail exhibits are registered",
          {"revenue_orders", "aov_conversion", "category_returns", "buyer_mix"}
          <= set(registered))

    retail = build_all([], retail_data.tables, mode="light")
    ids = {s.id for s in retail}
    check("a retail run draws its own exhibits",
          {"revenue_orders", "category_returns", "buyer_mix"} <= ids, str(ids))
    check("and none of the subscription ones",
          not ({"arr_bridge", "cohort_heatmap", "segment_churn"} & ids))

    saas_specs = build_all([], saas_data.tables, mode="light")
    saas_ids = {s.id for s in saas_specs}
    check("a subscription run draws none of the retail exhibits",
          not ({"revenue_orders", "aov_conversion", "category_returns",
                "buyer_mix"} & saas_ids), str(saas_ids))


# --------------------------------------------------------------------------
# 6. The knobs
# --------------------------------------------------------------------------

def test_knobs() -> None:
    print("\ngenerator knobs")

    profile = load_profile(SAAS)
    rnd_share = profile.financials.opex_split.get("rnd", 0.20)
    from kpi_maker.datagen.base import WARMUP_MONTHS

    def noise_std(volatility: float) -> float:
        """Recover the multiplicative noise, with the leverage trend divided out."""
        fin = GENERATORS["saas"](
            profile, GeneratorParams(volatility=volatility)
        ).tables["monthly_financials"]
        total = len(fin) + WARMUP_MONTHS
        t = np.arange(WARMUP_MONTHS, total)
        leverage = 1.0 + 0.25 * (1 - t / (total - 1))
        noise = fin["rnd_cost"].to_numpy() / (
            fin["revenue"].to_numpy() * rnd_share * leverage)
        return float(noise.std())

    calm, base, wild = noise_std(0.0), noise_std(1.0), noise_std(2.5)
    check("volatility 0 removes the noise entirely", calm < 1e-9, f"{calm:.2e}")
    check("volatility scales the spread proportionally",
          abs(wild / max(base, 1e-9) - 2.5) < 0.35, f"{base:.4f} -> {wild:.4f}")

    flat = apply_amplitude(np.array([0.8, 1.2]), 0.0)
    check("amplitude 0 flattens the curve", np.allclose(flat, 1.0), str(flat))
    doubled = apply_amplitude(np.array([0.8, 1.2]), 2.0)
    check("amplitude scales the deviation, not the level",
          np.allclose(doubled, [0.6, 1.4]) and abs(doubled.mean() - 1.0) < 1e-9,
          str(doubled))

    with_events = GENERATORS["saas"](profile, GeneratorParams())
    without = GENERATORS["saas"](profile, GeneratorParams(inject_anomalies=False))
    check("anomalies are planted by default", len(with_events.anomalies) == 3)
    check("and can be turned off", len(without.anomalies) == 0)
    check("a run with no planted events still reconciles",
          len(without.checks) == len(with_events.checks))

    # Defaults must not perturb anything — that is what keeps the samples
    # byte-identical.
    explicit = GeneratorParams(seasonality_amplitude=1.0, volatility=1.0,
                               inject_anomalies=True)
    a = GENERATORS["saas"](profile, explicit).tables["monthly_financials"]
    b = GENERATORS["saas"](profile, None).tables["monthly_financials"]
    check("passing the defaults explicitly changes nothing",
          a.equals(b))

    # The knobs reach the e-commerce generator too, or they are a subscription
    # feature wearing a general name.
    retail = load_profile(RETAIL)
    quiet = GENERATORS["ecommerce"](retail, GeneratorParams(inject_anomalies=False))
    check("e-commerce honours the anomaly switch", len(quiet.anomalies) == 0)


# --------------------------------------------------------------------------

def run() -> int:
    print("=" * 70)
    print("SECTOR BREADTH")
    print("=" * 70)

    tmp = Path(tempfile.mkdtemp(prefix="sector-test-"))
    try:
        saas_data = GENERATORS["saas"](load_profile(SAAS))
        retail_data = GENERATORS["ecommerce"](load_profile(RETAIL))
        suites = [
            test_every_sector_runs,
            test_registry,
            lambda: test_vacuity_guard(saas_data),
            lambda: test_contract_scoping(saas_data, retail_data),
            lambda: test_ecommerce(retail_data),
            lambda: test_pack(retail_data),
            lambda: test_exhibits(retail_data, saas_data),
            test_knobs,
        ]
        for suite in suites:
            try:
                suite()
            except Exception as exc:                        # noqa: BLE001
                _failures.append(f"crash: {type(exc).__name__}: {exc}")
                print(f"  CRASH  {type(exc).__name__}: {exc}")
                traceback.print_exc(limit=4)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'=' * 70}")
    print(f"checks: {_checks}  failures: {len(_failures)}")
    for failure in _failures:
        print(f"  - {failure}")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(run())

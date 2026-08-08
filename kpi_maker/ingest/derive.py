"""Read the profile off the data, so the survey only asks what it must.

The survey's own design rule is "never ask what we can derive"
(ARCHITECTURE §1.2). A transaction log already contains the revenue, the
customer count, the currency and the reporting period — asking for them again
is both rude and less accurate than reading them.

Everything derived here is tagged in `_provenance` as `ingested:<file>`, so the
report appendix can distinguish a number taken from the user's data from one
they typed and one we assumed. That distinction is the same discipline as
`basis` on a metric, applied to the profile.

What cannot be derived is genuinely not in the file: sector, objective and
audience are judgements about intent, and no export contains them.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# Currency symbols and codes worth recognising in a money column.
_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "₺": "TRY", "¥": "JPY"}
_CODES = {"USD", "EUR", "GBP", "TRY", "JPY", "CHF", "SEK", "NOK", "DKK",
          "CAD", "AUD", "INR", "AED", "PLN", "CZK"}

REVENUE_BANDS = [
    (1_000_000, "<1M"), (10_000_000, "1-10M"), (50_000_000, "10-50M"),
    (250_000_000, "50-250M"), (float("inf"), "250M+"),
]


class Derived:
    """What the data says, and what it could not say."""

    def __init__(self) -> None:
        self.values: Dict[str, Any] = {}
        self.provenance: Dict[str, str] = {}
        self.notes: List[str] = []
        self.unknown: List[str] = []

    def set(self, path: str, value: Any, source: str, note: str = "") -> None:
        self.values[path] = value
        self.provenance[path] = f"ingested:{source}"
        if note:
            self.notes.append(note)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "values": self.values, "provenance": self.provenance,
            "notes": self.notes, "still_needed": self.unknown,
        }


# Fields no file contains: they are statements of intent, not facts about the
# past. The shortened survey asks for exactly these.
NOT_IN_ANY_FILE = [
    ("industry.internal_sector", "What sector are you in?"),
    ("intent.primary_objective", "What is the main objective for the next 12 months?"),
    ("intent.audience", "Who reads this?"),
    ("business_model.customer_type", "Who buys — B2B, B2C, B2B2C or B2G?"),
]


def derive_profile_fields(tables: Dict[str, pd.DataFrame],
                          filename: str = "upload") -> Derived:
    """Everything the uploaded fact tables can tell us about the company."""
    out = Derived()

    _derive_period(out, tables, filename)
    _derive_revenue(out, tables, filename)
    _derive_customers(out, tables, filename)
    _derive_currency(out, tables, filename)
    _derive_segments(out, tables, filename)

    out.unknown = [
        {"path": path, "question": question}
        for path, question in NOT_IN_ANY_FILE
        if path not in out.values
    ]
    return out


def _months(tables: Dict[str, pd.DataFrame]) -> Optional[pd.Series]:
    for name in ("monthly_financials", "mrr_movements", "pipeline", "headcount"):
        frame = tables.get(name)
        if frame is not None and "month" in frame.columns and not frame.empty:
            return pd.PeriodIndex(frame["month"].astype(str), freq="M").to_series()
    return None


def _derive_period(out: Derived, tables, filename: str) -> None:
    months = _months(tables)
    if months is None or months.empty:
        return
    span = int(months.max().ordinal - months.min().ordinal) + 1
    out.set("history_months", span, filename,
            f"Read {span} month(s) of history, {months.min()} to {months.max()}.")


def _derive_revenue(out: Derived, tables, filename: str) -> None:
    """Annual revenue: the last twelve months, not the file total.

    A file covering three years would otherwise report triple the company's
    actual size, and every per-revenue benchmark would be wrong by that factor.
    """
    fin = tables.get("monthly_financials")
    if fin is not None and "revenue" in fin.columns and not fin.empty:
        revenue = pd.to_numeric(fin["revenue"], errors="coerce").dropna()
        if not revenue.empty:
            annual = float(revenue.tail(12).sum())
            out.set("financials.revenue", annual, filename,
                    f"Summed the last {min(len(revenue), 12)} month(s) of revenue "
                    f"to {annual:,.0f}.")
            out.set("size.revenue_band", _band(annual), filename)
            _derive_margin(out, fin, filename)
            return

    movements = tables.get("mrr_movements")
    if movements is not None and "delta_mrr" in movements.columns and not movements.empty:
        # A movement log gives the run-rate, which annualises rather than sums.
        # Sum the POSITIVE closing books, not every delta: a customer whose
        # movements net below zero holds an impossible position, and letting
        # that offset a real one understates the book while contradicting the
        # customer count derived from the same data.
        closing = _closing_book(movements)
        if closing is None:
            return
        surviving = closing[closing > 0]
        annual = float(surviving.sum() * 12.0)
        if annual > 0:
            impossible = int((closing < 0).sum())
            note = (f"Annualised the closing book of {len(surviving):,} "
                    f"customer(s) to {annual:,.0f}.")
            if impossible:
                out.notes.append(
                    f"{impossible:,} customer(s) have more reductions than "
                    f"additions in this file, which cannot be a real position — "
                    f"check the sign convention on your amount column.")
            out.set("financials.revenue", annual, filename, note)
            out.set("size.revenue_band", _band(annual), filename)


def _derive_margin(out: Derived, fin: pd.DataFrame, filename: str) -> None:
    if "cogs" not in fin.columns:
        return
    revenue = pd.to_numeric(fin["revenue"], errors="coerce").tail(12).sum()
    cogs = pd.to_numeric(fin["cogs"], errors="coerce").tail(12).sum()
    if revenue and revenue > 0 and pd.notna(cogs):
        margin = float((revenue - cogs) / revenue)
        if -1.0 <= margin <= 1.0:
            out.set("financials.gross_margin_pct", round(margin, 4), filename,
                    f"Computed gross margin at {margin:.1%} from revenue and COGS.")


def _derive_customers(out: Derived, tables, filename: str) -> None:
    customers = tables.get("customers")
    if customers is not None and not customers.empty:
        if "is_active" in customers.columns:
            count = int(pd.Series(customers["is_active"]).astype(bool).sum())
        else:
            count = int(customers["customer_id"].nunique()
                        if "customer_id" in customers.columns else len(customers))
        if count:
            out.set("market.customer_count", count, filename,
                    f"Counted {count:,} customer(s).")
            return

    movements = tables.get("mrr_movements")
    if movements is not None and "customer_id" in movements.columns:
        # Customers with a positive closing book, not everyone who ever appears.
        # Revenue is the value of the surviving book, so counting churned-out
        # customers alongside it breaks the profile's own identity — customers x
        # blended ACV must approximate revenue — and every per-customer metric
        # with it.
        closing = _closing_book(movements)
        count = int((closing > 0).sum()) if closing is not None else \
            int(movements["customer_id"].nunique())
        if count:
            churned = (int(len(closing) - count) if closing is not None else 0)
            tail = (f" ({churned:,} more have churned out)" if churned else "")
            out.set("market.customer_count", count, filename,
                    f"Counted {count:,} customer(s) with an open book{tail}.")


def _derive_currency(out: Derived, tables, filename: str) -> None:
    """Guess the currency from symbols or an explicit code column."""
    for frame in tables.values():
        if frame is None or frame.empty:
            continue
        for column in frame.columns:
            name = str(column).lower()
            series = frame[column]
            if "currency" in name or name in ("ccy", "curr"):
                values = series.dropna().astype(str).str.upper().str.strip()
                codes = [v for v in values.unique() if v in _CODES]
                if len(codes) == 1:
                    out.set("identity.currency", codes[0], filename,
                            f"Read the currency as {codes[0]} from {column!r}.")
                    return
                if len(codes) > 1:
                    out.notes.append(
                        f"{column!r} holds {len(codes)} currencies "
                        f"({', '.join(sorted(codes))}) — the pipeline reports in one, "
                        f"so convert before running or the totals mix units.")
                    return
            if series.dtype == object:
                text = series.dropna().astype(str).head(50)
                for symbol, code in _SYMBOLS.items():
                    if text.str.contains(symbol, regex=False).mean() > 0.5:
                        out.set("identity.currency", code, filename,
                                f"Read the currency as {code} from the {symbol} "
                                f"symbol in {column!r}.")
                        return


def _derive_segments(out: Derived, tables, filename: str) -> None:
    """Segment mix and blended ACV.

    Derived from the same source as revenue and customer count, never mixed
    with a leftover assumption. The profile carries a cross-block identity —
    customers x blended ACV must approximate revenue — and taking two of those
    three from the file while leaving the third at a sample value guarantees it
    fails validation. Worse, if it did not fail, every per-customer metric
    would be quietly wrong.
    """
    frame = _segment_acv_from_customers(tables)
    if frame is None:
        frame = _segment_acv_from_movements(tables)
    if frame is None or frame.empty:
        return

    grouped = frame.groupby("segment")["acv"].agg(["count", "mean"])
    total = int(grouped["count"].sum())
    if not total:
        return

    segments = [
        {
            "name": str(name),
            "share": round(float(row["count"]) / total, 4),
            "avg_acv": round(float(row["mean"]), 2),
            # Not in the data: churn needs a history of departures, and a
            # snapshot has none. Left at zero rather than invented, and the
            # survey can override it.
            "logo_churn_annual": 0.0,
        }
        for name, row in grouped.iterrows()
    ]
    # Shares must sum to exactly 1.0 or the profile validator rejects them;
    # rounding four of them independently rarely does.
    drift = 1.0 - sum(s["share"] for s in segments)
    if segments:
        segments[0]["share"] = round(segments[0]["share"] + drift, 4)

    out.set("market.segments", segments, filename,
            f"Read {len(segments)} segment(s) from the customer file.")


def _segment_acv_from_customers(tables) -> Optional[pd.DataFrame]:
    customers = tables.get("customers")
    if customers is None or customers.empty:
        return None
    if "segment" not in customers.columns or "final_acv" not in customers.columns:
        return None
    return pd.DataFrame({
        "segment": customers["segment"].astype(str),
        "acv": pd.to_numeric(customers["final_acv"], errors="coerce"),
    }).dropna()


def _closing_book(movements: pd.DataFrame) -> Optional[pd.Series]:
    """Each customer's closing MRR — the sum of every movement against them."""
    if "customer_id" not in movements.columns or "delta_mrr" not in movements.columns:
        return None
    values = pd.to_numeric(movements["delta_mrr"], errors="coerce").fillna(0.0)
    return values.groupby(movements["customer_id"]).sum()


def _segment_acv_from_movements(tables) -> Optional[pd.DataFrame]:
    """One ACV per customer, from the sum of their movements, annualised.

    A transaction log has no per-customer ACV column, but it has every change
    to that customer's book. Summing them gives the closing position, which is
    the same quantity the customers table would have stated directly.
    """
    movements = tables.get("mrr_movements")
    if movements is None or movements.empty:
        return None
    needed = {"segment", "customer_id", "delta_mrr"}
    if not needed.issubset(set(movements.columns)):
        return None

    frame = movements.copy()
    frame["delta_mrr"] = pd.to_numeric(frame["delta_mrr"], errors="coerce")
    per_customer = frame.groupby("customer_id").agg(
        acv=("delta_mrr", "sum"),
        # A customer's segment is whatever it was most recently recorded as.
        segment=("segment", "last"),
    ).reset_index()
    per_customer["acv"] = per_customer["acv"] * 12.0
    per_customer = per_customer[per_customer["acv"] > 0]
    return per_customer[["segment", "acv"]].dropna() if not per_customer.empty else None


def _band(revenue: float) -> str:
    for ceiling, label in REVENUE_BANDS:
        if revenue < ceiling:
            return label
    return "250M+"


def apply_to_profile(profile, derived: Derived):
    """Layer derived values over a profile, keeping provenance.

    Returns a new profile — the caller may still want the original to show a
    before/after in the quality report.
    """
    payload = profile.model_dump(mode="json")

    for path, value in derived.values.items():
        target = payload
        parts = path.split(".")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value

    payload.setdefault("provenance", {}).update(derived.provenance)

    from ..profile.schema import CompanyProfile
    return CompanyProfile(**payload)

"""Out-of-stock (OOS) view for the portfolio review.

Answers the question the OOS Impact dashboard (Margin-Analytics) raises, adapted
to this repo's monthly grain: **where are we spending marketing while a product
is out of stock?** Two signals, written to data/generated/oos.csv per SKU x
channel:

1. Current stock (live) — from the newest Novadata FBA snapshot in data/source/
   (nova_inventory_*.csv): available pool, days-of-inventory, sales velocity and
   Amazon's own oosRiskFlag. Amazon (FBA) only; the webshop has no stock feed.

2. Marketing-while-OOS —
   * NOW: a product currently at OOS risk that still drew ad spend last month
     (advertising_while_oos_now) — the "stop the spend" list.
   * HISTORY: a monthly demand-gap proxy — a month whose units collapsed to ~0
     against the SKU's own trailing baseline (so it was effectively out) while it
     booked ad spend. Summed as wasted_ad_spend_l6m. Amazon ad spend is reported
     from Feb 2026 only, so the historical window is short.

Run after build_portfolio.py:  python pipeline/oos.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.common import (  # noqa: E402
    COGS_CSV,
    FACTS_PATH,
    INVENTORY_GLOB,
    OOS_PATH,
    SOURCE_DIR,
    month_range,
)

# Demand-gap proxy tuning (monthly analogue of the OOS dashboard's daily rule).
MIN_BASELINE_UNITS = 5.0   # trailing avg units/month below this = slow mover, ignore
COLLAPSE_FRAC = 0.15       # a month under this fraction of baseline units = "out"
LOW_STOCK_DAYS = 14        # days-of-inventory at/under this = critically low (current)
# Slow-mover / dead-stock tuning (stock sitting, not selling).
SLOW_DOI = 180             # >= this many days of supply on hand = slow / overstocked
OVERSTOCK_DOI = 270        # >= this = badly overstocked (long-term storage-fee risk)


def load_unit_cogs() -> pd.DataFrame:
    """Median COGS per SKU (to value stock tied up in slow movers)."""
    try:
        c = pd.read_csv(COGS_CSV, usecols=["sku", "COGS"])
    except Exception:
        return pd.DataFrame(columns=["sku", "cogs_unit"])
    return c.groupby("sku", as_index=False)["COGS"].median().rename(
        columns={"COGS": "cogs_unit"}
    )


def stock_reco(r: pd.Series) -> str:
    """Plain-language action for a slow mover / dead stock row."""
    if not r["slow_mover"]:
        return ""
    av = r["fba_available"] or 0
    doi = r["days_of_inventory"]
    if r["dead_stock"]:
        return (f"Dead stock: {av:,.0f} units on hand, no sales in 30 days — raise a "
                "removal/liquidation order and stop reordering")
    months = doi / 30.0 if pd.notna(doi) else None
    if pd.notna(doi) and doi >= OVERSTOCK_DOI:
        return (f"Badly overstocked (~{months:.0f} months of supply) — markdown or promo "
                "to clear, halt reorders; watch long-term storage fees")
    return (f"Slow mover (~{months:.0f} months of supply) — cut reorder quantity, "
            "consider a markdown to free up capital")


def latest_inventory() -> pd.DataFrame:
    """Newest FBA snapshot, one row per SKU (Amazon stock health)."""
    files = sorted(SOURCE_DIR.glob(INVENTORY_GLOB))
    if not files:
        return pd.DataFrame(columns=["sku"])
    inv = pd.read_csv(files[-1])
    inv["snapshot"] = files[-1].name.replace("nova_inventory_", "").replace(".csv", "")
    # one row per SKU (snapshot can repeat a SKU across stock regions)
    agg = inv.groupby("sku", as_index=False).agg(
        fba_available=("totalFbaAvailablePool", "max"),
        fba_incoming=("totalFbaIncomingPool", "max"),
        sales_velocity=("salesVelocity", "max"),
        days_of_inventory=("daysOfInventory", "min"),
        oos_risk=("oosRiskFlag", "max"),
        units_30d=("totalUnits30d", "max"),
        snapshot=("snapshot", "first"),
    )
    agg["oos_risk"] = agg["oos_risk"].fillna(False).astype(bool)
    return agg


def historical_wasted(facts: pd.DataFrame, review_month: str) -> pd.DataFrame:
    """Per SKU x channel: ad spend booked in demand-gap OOS months (last 6m)."""
    l6 = set(month_range(review_month, 6))
    df = facts.sort_values("month").copy()
    df["units"] = pd.to_numeric(df["units"], errors="coerce")
    df["ad_spend"] = pd.to_numeric(df["ad_spend"], errors="coerce").fillna(0.0)
    keys = ["brand", "channel", "country", "sku"]
    # trailing 3-month average units, strictly before the current month
    df["baseline_units"] = (
        df.groupby(keys)["units"]
        .transform(lambda s: s.shift(1).rolling(3, min_periods=2).mean())
    )
    sold_after = df.groupby(keys)["units"].transform(
        lambda s: s[::-1].shift(1).rolling(12, min_periods=1).sum()[::-1]
    )
    oos = (
        (df["baseline_units"] >= MIN_BASELINE_UNITS)
        & (df["units"].fillna(0) <= COLLAPSE_FRAC * df["baseline_units"])
        & (sold_after > 0)                      # enclosed by later sales, not a dead tail
        & (df["month"].isin(l6))
    )
    df["oos_month"] = oos
    df["wasted"] = df["ad_spend"].where(oos & (df["ad_spend"] > 0), 0.0)
    out = (
        df.groupby(["brand", "channel", "sku"])
        .agg(wasted_ad_spend_l6m=("wasted", "sum"),
             oos_months_l6m=("oos_month", "sum"))
        .reset_index()
    )
    out["oos_months_l6m"] = out["oos_months_l6m"].astype(int)
    return out


def main() -> Path:
    facts = pd.read_csv(FACTS_PATH)
    review_month = sorted(facts.loc[facts["in_scope"], "month"].unique())[-2]

    # last-month ad spend + product name per SKU x channel
    last_ad = (
        facts[facts["month"] == review_month]
        .assign(ad_spend=lambda d: pd.to_numeric(d["ad_spend"], errors="coerce").fillna(0.0))
        .groupby(["brand", "channel", "sku"], as_index=False)
        .agg(recent_ad_spend=("ad_spend", "sum"),
             recent_units=("units", "sum"),
             product=("product", "first"))
    )

    wasted = historical_wasted(facts, review_month)
    inv = latest_inventory()

    oos = last_ad.merge(wasted, on=["brand", "channel", "sku"], how="outer")
    oos = oos.merge(inv, on="sku", how="left")
    # stock signals are Amazon (FBA) only
    non_amazon = oos["channel"] != "Amazon"
    for c in ["fba_available", "fba_incoming", "sales_velocity", "days_of_inventory",
              "oos_risk", "units_30d"]:
        if c in oos:
            oos.loc[non_amazon, c] = pd.NA

    oos["oos_risk"] = oos["oos_risk"].fillna(False).astype(bool)
    oos["recent_ad_spend"] = oos["recent_ad_spend"].fillna(0.0)
    oos["wasted_ad_spend_l6m"] = oos["wasted_ad_spend_l6m"].fillna(0.0)
    oos["oos_months_l6m"] = oos["oos_months_l6m"].fillna(0).astype(int)
    amazon = oos["channel"] == "Amazon"

    # OUT NOW: nothing sellable in the pool - a listing you can't buy from.
    oos["out_now"] = amazon & (oos["fba_available"].fillna(0) <= 0)
    # LOW STOCK: still sellable but reach is short (Amazon risk flag or <= LOW_STOCK_DAYS).
    oos["low_stock"] = amazon & ~oos["out_now"] & (
        oos["oos_risk"] | (oos["days_of_inventory"] <= LOW_STOCK_DAYS)
    )
    # THE waste signal: out of stock AND still advertised -> stop the campaigns.
    oos["advertising_while_out_now"] = oos["out_now"] & (oos["recent_ad_spend"] > 0)
    # reorder-urgency: selling but running low while advertised (protect, don't stop).
    oos["advertising_while_low"] = oos["low_stock"] & (oos["recent_ad_spend"] > 0)

    # slow movers / dead stock: sellable units on hand that aren't moving.
    units_30d = pd.to_numeric(oos["units_30d"], errors="coerce")
    has_stock = amazon & (oos["fba_available"].fillna(0) > 0)
    oos["dead_stock"] = has_stock & (units_30d.fillna(0) <= 0)
    oos["slow_mover"] = has_stock & (
        oos["dead_stock"] | (oos["days_of_inventory"] >= SLOW_DOI)
    )
    oos = oos.merge(load_unit_cogs(), on="sku", how="left")
    oos["tied_capital"] = (oos["fba_available"].fillna(0) * oos["cogs_unit"]).where(
        oos["slow_mover"]
    )
    oos["stock_reco"] = oos.apply(stock_reco, axis=1)
    oos["review_month"] = review_month

    OOS_PATH.parent.mkdir(parents=True, exist_ok=True)
    oos.to_csv(OOS_PATH, index=False)

    waste = oos["advertising_while_out_now"]
    print(f"OOS view: {len(oos)} SKU x channel rows -> {OOS_PATH}")
    print(f"  OUT of stock now (0 available): {int(oos['out_now'].sum())} "
          f"| of those still advertised: {int(waste.sum())} "
          f"(EUR {oos.loc[waste, 'recent_ad_spend'].sum():,.0f} last month)")
    print(f"  LOW stock (reorder) while advertised: {int(oos['advertising_while_low'].sum())}")
    print(f"  SLOW movers / dead stock: {int(oos['slow_mover'].sum())} "
          f"(dead {int(oos['dead_stock'].sum())}), "
          f"EUR {oos['tied_capital'].sum():,.0f} capital tied up")
    print(f"  historical wasted ad spend (l6m, demand-gap): "
          f"EUR {oos['wasted_ad_spend_l6m'].sum():,.0f} "
          f"over {int((oos['wasted_ad_spend_l6m'] > 0).sum())} SKUs")
    return OOS_PATH


if __name__ == "__main__":
    main()

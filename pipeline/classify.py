"""Classification engine: verdicts for a review month at two grains.

The review is driven at **SKU x channel** level (Amazon vs Shopify, countries
collapsed) - that is the primary output in verdicts.csv. Country marketplaces
are still classified individually (verdicts_country.csv) so the review can show
a compact per-country verdict summary and a full by-country tab without changing
the headline unit.

Reads config/rules.yaml (thresholds), config/ap26_targets.json (CM3 targets),
data/generated/facts_monthly.csv.gz and launch_dates.csv; writes
data/generated/verdicts.csv (channel grain) + verdicts_country.csv (country
grain) plus a dated channel-grain copy in verdict_history/ so packs can diff
month over month.

Manual overrides: data/overrides/verdict_overrides.csv
(columns: sku, country, verdict, note). country="*" targets the channel-grain
row; a country code targets that market. Overrides always win.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.common import (  # noqa: E402
    COUNTRY_VERDICTS_PATH,
    FACTS_PATH,
    GENERATED_DIR,
    HISTORY_DIR,
    LAUNCH_PATH,
    OVERRIDES_DIR,
    RULES_YAML,
    VERDICTS_PATH,
    cm3_target_for,
    load_targets,
    month_range,
)

# Grain definitions: (groupby keys, keys the relative volume/margin tiers are
# ranked within). Country tiers rank inside a single marketplace; channel tiers
# rank across the whole channel (all countries pooled).
CHANNEL_KEYS = ["brand", "channel", "sku"]
COUNTRY_KEYS = ["brand", "channel", "country", "sku"]


def month_diff(a: str, b: str) -> int:
    """Whole months from month b to month a (YYYY-MM strings)."""
    return (int(a[:4]) - int(b[:4])) * 12 + (int(a[5:7]) - int(b[5:7]))


def tier_1_to_3(values: pd.Series) -> pd.Series:
    """Terciles 1 (top) .. 3 (bottom); same convention as Margin-Analytics."""
    s = pd.to_numeric(values, errors="coerce")
    if s.notna().sum() < 3:
        return pd.Series(pd.NA, index=values.index, dtype="Int64")
    ranks = s.rank(method="first", ascending=False)
    try:
        return pd.qcut(ranks, q=3, labels=[1, 2, 3]).astype("Int64")
    except ValueError:
        return pd.Series(pd.NA, index=values.index, dtype="Int64")


def build_features(facts: pd.DataFrame, launch: pd.DataFrame, review_month: str,
                   cfg: dict, keys: list[str], tier_keys: list[str]) -> pd.DataFrame:
    """Per-group metrics as of the review month, at the grain given by `keys`.

    Windowed sums group by `keys`, so passing CHANNEL_KEYS collapses the country
    marketplaces into one channel row automatically; COUNTRY_KEYS keeps them
    separate. Relative volume/margin tiers are ranked within `tier_keys`.
    """
    r = cfg["review"]
    trend_n = r["trend_window_months"]
    margin_n = r["margin_window_months"]

    df = facts[(facts["in_scope"]) & (facts["month"] <= review_month)].copy()

    l3 = set(month_range(review_month, trend_n))
    p3 = set(month_range(str(pd.Period(review_month) - trend_n), trend_n))
    l6 = set(month_range(review_month, margin_n))
    p6 = set(month_range(str(pd.Period(review_month) - margin_n), margin_n))
    ttm = set(month_range(review_month, 12))
    l2 = set(month_range(review_month, 2))

    def window_sum(cols: list[str], months: set, suffix: str) -> pd.DataFrame:
        sub = df[df["month"].isin(months)]
        agg = sub.groupby(keys)[cols].sum(min_count=1)
        return agg.rename(columns={c: f"{c}_{suffix}" for c in cols})

    # grain-safe base: product/cm_source from the latest month; months_active and
    # last sale counted as DISTINCT months (a channel row spans several countries
    # per month, so a plain row count would multi-count).
    df_sorted = df.sort_values("month")
    base = df_sorted.groupby(keys).agg(
        product=("product", "last"),
        cm_source=("cm_source", "last"),
    )
    sold = df[df["net_sales"] > 0]
    base["months_active"] = sold.groupby(keys)["month"].nunique()
    base["last_sale_month"] = sold.groupby(keys)["month"].max()
    base["months_active"] = base["months_active"].fillna(0).astype(int)

    parts = [
        window_sum(["net_sales"], l3, "l3m"),
        window_sum(["net_sales"], p3, "p3m"),
        window_sum(["net_sales", "cm1", "cm2", "cm3", "ad_spend"], l6, "l6m"),
        window_sum(["cm3", "net_sales"], p6, "p6m"),
        window_sum(["net_sales", "cm3"], ttm, "ttm"),
        window_sum(["net_sales"], l2, "l2m"),
    ]
    feat = base.join(parts).reset_index()

    # months with negative CM3 inside the margin window (needs actual/estimated CM)
    neg = (
        df[df["month"].isin(l6) & df["cm3"].notna() & (df["cm3"] < 0)]
        .groupby(keys)["month"].nunique().rename("neg_cm3_months")
    )
    cm_months = (
        df[df["month"].isin(l6) & df["cm3"].notna()]
        .groupby(keys)["month"].nunique().rename("cm_months_l6m")
    )
    feat = feat.merge(neg, on=keys, how="left").merge(cm_months, on=keys, how="left")
    feat["neg_cm3_months"] = feat["neg_cm3_months"].fillna(0).astype(int)
    feat["cm_months_l6m"] = feat["cm_months_l6m"].fillna(0).astype(int)

    feat["cm3_pct_l6m"] = feat["cm3_l6m"] / feat["net_sales_l6m"].where(feat["net_sales_l6m"] > 0) * 100
    feat["cm3_pct_p6m"] = feat["cm3_p6m"] / feat["net_sales_p6m"].where(feat["net_sales_p6m"] > 0) * 100
    feat["cm1_pct_l6m"] = feat["cm1_l6m"] / feat["net_sales_l6m"].where(feat["net_sales_l6m"] > 0) * 100
    feat["cm2_pct_l6m"] = feat["cm2_l6m"] / feat["net_sales_l6m"].where(feat["net_sales_l6m"] > 0) * 100
    feat["cm3_improving_pp"] = feat["cm3_pct_l6m"] - feat["cm3_pct_p6m"]
    feat["sales_trend_pct"] = (
        (feat["net_sales_l3m"] - feat["net_sales_p3m"])
        / feat["net_sales_p3m"].where(feat["net_sales_p3m"] > 0) * 100
    )

    # launch / age
    feat = feat.merge(
        launch.rename(columns={"first_sale_month": "launch_month"}),
        on="sku", how="left",
    )
    feat["age_months"] = feat["launch_month"].map(
        lambda m: month_diff(review_month, m) if isinstance(m, str) else np.nan
    )

    # relative tiers within each tier group (brand-agnostic, like the 3x3 matrix)
    grp = feat.groupby(tier_keys, group_keys=False)
    feat["vol_tier"] = grp["net_sales_ttm"].apply(tier_1_to_3)
    feat["margin_tier"] = grp["cm3_pct_l6m"].apply(tier_1_to_3)
    feat["cluster"] = (
        feat["margin_tier"].astype("string") + "-" + feat["vol_tier"].astype("string")
    )
    return feat


def channel_target_blend(facts: pd.DataFrame, review_month: str, targets: dict) -> pd.DataFrame:
    """Sales-weighted CM3 target per SKU x channel: each country's AP26 target
    weighted by that SKU's TTM net sales in the country."""
    ttm = set(month_range(review_month, 12))
    sub = facts[facts["in_scope"] & facts["month"].isin(ttm)]
    w = (
        sub.groupby(CHANNEL_KEYS + ["country"], as_index=False)["net_sales"].sum()
    )
    w["net_sales"] = w["net_sales"].clip(lower=0)
    w["tgt"] = w["country"].map(lambda c: cm3_target_for(c, targets))
    w = w.dropna(subset=["tgt"])

    def blend(g: pd.DataFrame) -> float:
        s = g["net_sales"].sum()
        if s <= 0:
            return float(g["tgt"].mean())
        return float((g["tgt"] * g["net_sales"]).sum() / s)

    out = w.groupby(CHANNEL_KEYS).apply(blend, include_groups=False)
    return out.rename("cm3_target").reset_index()


def is_incubating(row: pd.Series, cfg: dict) -> bool:
    """New product still inside the incubation window (and not censored)."""
    r = cfg["review"]
    return bool(
        pd.notna(row["age_months"])
        and row["age_months"] <= r["incubation_months"]
        and not row.get("censored", False)
    )


def classify_row(row: pd.Series, cfg: dict, apply_volume_floor: bool = False) -> tuple[str, str]:
    """Return one of the five verdicts (Scale/Defend/Fix/Harvest/Exit) or the
    'No data' state. New products are shielded: a would-be Fix/Exit is held as
    Defend while they ramp (the 'New' tag carries the context).

    apply_volume_floor (channel grain only): a whole product below
    thresholds.discontinue_below_ttm is discontinued (Exit), unless it is a New
    launch (shielded) or still growing."""
    r, t = cfg["review"], cfg["thresholds"]
    reasons: list[str] = []

    target = row["cm3_target"] if pd.notna(row.get("cm3_target")) else None
    cm3 = row["cm3_pct_l6m"]
    has_cm = row["cm_months_l6m"] > 0 and pd.notna(cm3)
    trend = row["sales_trend_pct"]
    growing = pd.notna(trend) and trend >= t["scale_growth_pct"]
    declining = pd.notna(trend) and trend <= t["decline_pct"]
    improving = pd.notna(row["cm3_improving_pp"]) and row["cm3_improving_pp"] >= t["improving_pp"]
    vol_tier = int(row["vol_tier"]) if pd.notna(row["vol_tier"]) else None
    top_vol = vol_tier in (1, 2)
    stalled = (
        (row["net_sales_l2m"] or 0) <= 0
        and (row["net_sales_ttm"] or 0) >= t["stall_min_ttm_sales"]
    )
    if stalled:
        reasons.append("no sales in last 2 months (OOS / delisted?)")

    # 0. states (not verdicts)
    if row["months_active"] < r["min_months_data"]:
        return "No data", "fewer than %d months with sales" % r["min_months_data"]
    if not has_cm:
        return "No data", "; ".join(["no margin data for this market"] + reasons)

    young = is_incubating(row, cfg)

    def finish(verdict: str) -> tuple[str, str]:
        # incubation shield: never Fix/Exit a fresh launch - hold as Defend
        if young:
            tag = f"new: launched {row['launch_month']} ({int(row['age_months'])}m ago)"
            if verdict in ("Fix", "Exit"):
                reasons.insert(0, f"would be {verdict} but shielded while ramping")
                verdict = "Defend"
            reasons.insert(0, tag)
        return verdict, "; ".join(reasons)

    # 0b. volume floor: a whole product too small to keep (channel grain only,
    # Amazon only - webshop revenue/CM is estimated, so the hard floor is N/A there).
    # New launches are shielded by finish(); a SKU whose recent run-rate is
    # already on pace to clear the floor (annualised last-3m >= floor) is spared.
    floor = t.get("discontinue_below_ttm")
    ttm_sales = row["net_sales_ttm"] or 0
    run_rate = (row["net_sales_l3m"] or 0) * 4  # annualised recent pace
    if (apply_volume_floor and row["channel"] == "Amazon" and floor
            and ttm_sales < floor and run_rate < floor):
        reasons.insert(0, f"TTM sales EUR {ttm_sales:,.0f} < EUR {floor:,.0f} floor "
                          f"(run-rate EUR {run_rate:,.0f}/yr) - discontinue")
        return finish("Exit")

    # 1. exit: persistently losing money and not turning
    # (requires the 6m window to be negative OVERALL, so storage fees during a
    # stock-out don't send an otherwise healthy product to Exit)
    if (
        row["neg_cm3_months"] >= t["exit_neg_months"]
        and pd.notna(cm3) and cm3 < 0
        and not improving
    ):
        reasons.insert(0, f"CM3 negative in {int(row['neg_cm3_months'])} of last "
                          f"{r['margin_window_months']} months, not improving")
        if vol_tier == 1:
            # a top-volume money loser gets one repair cycle before delisting
            reasons.append("top volume tier: EXIT CANDIDATE if fix fails next review")
            return finish("Fix")
        return finish("Exit")

    below_gap = target is not None and pd.notna(cm3) and cm3 < target - t["fix_gap_pp"]
    below_target = target is not None and pd.notna(cm3) and cm3 < target

    # 2. fix: margin broken but worth saving
    if below_gap or (pd.notna(cm3) and cm3 < 0):
        gap = f"CM3% {cm3:.1f} vs target {target:.1f}" if target is not None else f"CM3% {cm3:.1f}"
        reasons.insert(0, gap)
        if top_vol or improving or growing:
            if improving:
                reasons.append(f"margin improving ({row['cm3_improving_pp']:+.1f}pp)")
            return finish("Fix")
        reasons.append("bottom volume tier, no momentum")
        return finish("Harvest")

    # 3. harvest: profitable, below target, small and fading
    if below_target and vol_tier == 3 and not growing:
        reasons.insert(0, f"CM3% {cm3:.1f} below target {target:.1f}, low volume, "
                          "flat/declining")
        return finish("Harvest")

    # 4. at/above target
    if target is None or cm3 >= target:
        if stalled:
            reasons.insert(0, "healthy margin but supply stalled")
            return finish("Fix")
        if growing or (top_vol and not declining):
            reasons.insert(0, f"CM3% {cm3:.1f} >= target"
                           + (f", sales {trend:+.0f}% 3m" if pd.notna(trend) else ""))
            return finish("Scale")
        reasons.insert(0, f"CM3% {cm3:.1f} >= target, momentum "
                          f"{'negative' if declining else 'flat'} - hold")
        return finish("Defend")

    # 5. between the gap and target - hold (was 'Watch')
    reasons.insert(0, "between target and broken - hold, re-check next month")
    return finish("Defend")


def classify_frame(feat: pd.DataFrame, cfg: dict, apply_volume_floor: bool = False) -> pd.DataFrame:
    verdict, reasons = zip(*(classify_row(r, cfg, apply_volume_floor) for _, r in feat.iterrows()))
    feat = feat.copy()
    feat["verdict"] = verdict
    feat["reasons"] = reasons
    feat["incubating"] = feat.apply(lambda row: is_incubating(row, cfg), axis=1)
    return feat


def apply_overrides(verdicts: pd.DataFrame, grain: str) -> pd.DataFrame:
    """Manual overrides. grain='country' matches (sku, country); grain='channel'
    matches (sku) where the override country is '*' (channel-wide call)."""
    path = OVERRIDES_DIR / "verdict_overrides.csv"
    verdicts = verdicts.copy()
    verdicts["manual"] = False
    if not path.exists():
        return verdicts
    ov = pd.read_csv(path, dtype=str).dropna(subset=["sku", "country", "verdict"])
    for _, o in ov.iterrows():
        if grain == "country":
            if o["country"] == "*":
                continue
            mask = (verdicts["sku"] == o["sku"]) & (verdicts["country"] == o["country"])
        else:
            if o["country"] != "*":
                continue
            mask = verdicts["sku"] == o["sku"]
        verdicts.loc[mask, "verdict"] = o["verdict"]
        verdicts.loc[mask, "reasons"] = "MANUAL: " + str(o.get("note", "") or "")
        verdicts.loc[mask, "manual"] = True
    return verdicts


def load_latest_bsr() -> pd.DataFrame:
    """Latest Amazon Best Seller Rank per SKU from the Excel history (BSR is a
    display-group rank averaged across marketplaces, so it is per SKU, not per
    country). Novadata's live BSR feed is empty, so the workbook is the source."""
    path = GENERATED_DIR / "excel_history.csv.gz"
    if not path.exists():
        return pd.DataFrame(columns=["sku", "bsr", "bsr_month"])
    h = pd.read_csv(path)
    b = h[(h["metric"] == "bsr") & h["value"].notna() & (h["value"] > 0)]
    if b.empty:
        return pd.DataFrame(columns=["sku", "bsr", "bsr_month"])
    latest = b.sort_values("month").groupby("sku").tail(1)
    return latest[["sku", "value", "month"]].rename(
        columns={"value": "bsr", "month": "bsr_month"}
    )


def add_bsr(verdicts: pd.DataFrame, bsr: pd.DataFrame) -> pd.DataFrame:
    """Attach latest BSR to Amazon rows only (Shopify has no Amazon rank)."""
    out = verdicts.merge(bsr, on="sku", how="left")
    non_amazon = out["channel"] != "Amazon"
    out.loc[non_amazon, ["bsr", "bsr_month"]] = pd.NA
    return out


def summarise_country_verdicts(country_v: pd.DataFrame) -> pd.DataFrame:
    """One compact per-SKU-x-channel summary of the country verdicts, for the
    review tab chips: a 'DE:Fix|FR:Scale|IT:Watch' string plus counts."""
    def one(g: pd.DataFrame) -> pd.Series:
        pairs = sorted(zip(g["country"], g["verdict"]))
        return pd.Series({
            "country_verdicts": "|".join(f"{c}:{v}" for c, v in pairs),
            "n_markets": len(pairs),
            "n_markets_action": int(g["verdict"].isin(["Fix", "Exit"]).sum()),
        })

    return country_v.groupby(CHANNEL_KEYS).apply(one, include_groups=False).reset_index()


def main(review_month: str | None = None) -> pd.DataFrame:
    with open(RULES_YAML) as fh:
        cfg = yaml.safe_load(fh)
    targets = load_targets()

    facts = pd.read_csv(FACTS_PATH)
    launch = pd.read_csv(LAUNCH_PATH)

    if review_month is None:
        # last complete month with in-scope data
        complete = sorted(facts.loc[facts["in_scope"], "month"].unique())
        review_month = complete[-2] if len(complete) > 1 else complete[-1]
    print(f"Review month: {review_month}")

    # ---- country grain (per marketplace) ----
    feat_c = build_features(facts, launch, review_month, cfg,
                            COUNTRY_KEYS, ["channel", "country"])
    feat_c["cm3_target"] = feat_c["country"].map(lambda c: cm3_target_for(c, targets))
    bsr = load_latest_bsr()
    country_v = classify_frame(feat_c, cfg, apply_volume_floor=False)
    country_v["review_month"] = review_month
    country_v = add_bsr(country_v, bsr)
    country_v = apply_overrides(country_v, "country")

    # ---- channel grain (countries collapsed) = primary review unit ----
    feat_ch = build_features(facts, launch, review_month, cfg,
                             CHANNEL_KEYS, ["channel"])
    feat_ch = feat_ch.merge(
        channel_target_blend(facts, review_month, targets), on=CHANNEL_KEYS, how="left"
    )
    channel_v = classify_frame(feat_ch, cfg, apply_volume_floor=True)
    channel_v["review_month"] = review_month
    channel_v = add_bsr(channel_v, bsr)
    channel_v = channel_v.merge(summarise_country_verdicts(country_v),
                                on=CHANNEL_KEYS, how="left")
    channel_v = apply_overrides(channel_v, "channel")

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    channel_v.to_csv(VERDICTS_PATH, index=False)
    channel_v.to_csv(HISTORY_DIR / f"verdicts_{review_month}.csv", index=False)
    country_v.to_csv(COUNTRY_VERDICTS_PATH, index=False)

    print("Channel grain (primary):")
    print(channel_v["verdict"].value_counts().to_string())
    print(f"-> {VERDICTS_PATH}  ({len(channel_v)} SKU x channel)")
    print(f"-> {COUNTRY_VERDICTS_PATH}  ({len(country_v)} SKU x country)")
    return channel_v


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)

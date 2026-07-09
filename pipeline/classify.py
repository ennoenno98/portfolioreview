"""Classification engine: one verdict per SKU x market for a review month.

Reads config/rules.yaml (thresholds), config/ap26_targets.json (CM3 targets),
data/generated/facts_monthly.csv.gz and launch_dates.csv; writes
data/generated/verdicts.csv plus a dated copy in verdict_history/ so packs can
diff month over month.

Manual overrides: data/overrides/verdict_overrides.csv
(columns: sku, country, verdict, note) always win and are marked as manual.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.common import (  # noqa: E402
    FACTS_PATH,
    HISTORY_DIR,
    LAUNCH_PATH,
    OVERRIDES_DIR,
    RULES_YAML,
    VERDICTS_PATH,
    cm3_target_for,
    load_targets,
    month_range,
)


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


def build_features(facts: pd.DataFrame, launch: pd.DataFrame,
                   review_month: str, cfg: dict) -> pd.DataFrame:
    """Per SKU x market metrics as of the review month."""
    r = cfg["review"]
    trend_n = r["trend_window_months"]
    margin_n = r["margin_window_months"]

    df = facts[(facts["in_scope"]) & (facts["month"] <= review_month)].copy()
    keys = ["brand", "channel", "country", "sku"]

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

    base = df.groupby(keys).agg(
        product=("product", "last"),
        months_active=("net_sales", lambda s: int((s > 0).sum())),
        last_sale_month=("month", "max"),
        cm_source=("cm_source", "last"),
    )
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

    # relative tiers within each market (brand-agnostic, like the 3x3 matrix)
    grp = feat.groupby(["channel", "country"], group_keys=False)
    feat["vol_tier"] = grp["net_sales_ttm"].apply(tier_1_to_3)
    feat["margin_tier"] = grp["cm3_pct_l6m"].apply(tier_1_to_3)
    feat["cluster"] = (
        feat["margin_tier"].astype("string") + "-" + feat["vol_tier"].astype("string")
    )
    return feat


def classify_row(row: pd.Series, cfg: dict, targets: dict) -> tuple[str, str]:
    r, t = cfg["review"], cfg["thresholds"]
    reasons: list[str] = []

    target = cm3_target_for(row["country"], targets)
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

    # 0. not enough history
    if row["months_active"] < r["min_months_data"]:
        return "No data", "fewer than %d months with sales" % r["min_months_data"]
    if not has_cm:
        return "No data", "; ".join(["no margin data for this market"] + reasons)

    # 1. incubation shield
    young = pd.notna(row["age_months"]) and row["age_months"] <= r["incubation_months"]
    if young and not row.get("censored", False):
        reasons.insert(0, f"launched {row['launch_month']} ({int(row['age_months'])}m ago)")
        if pd.notna(cm3) and target is not None and cm3 >= target:
            reasons.append("already at target CM3% - candidate for early Scale")
        return "Incubate", "; ".join(reasons)

    # 2. exit: persistently losing money and not turning
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
            return "Fix", "; ".join(reasons)
        return "Exit", "; ".join(reasons)

    below_gap = target is not None and pd.notna(cm3) and cm3 < target - t["fix_gap_pp"]
    below_target = target is not None and pd.notna(cm3) and cm3 < target

    # 3. fix: margin broken but worth saving
    if below_gap or (pd.notna(cm3) and cm3 < 0):
        gap = f"CM3% {cm3:.1f} vs target {target:.1f}" if target is not None else f"CM3% {cm3:.1f}"
        reasons.insert(0, gap)
        if top_vol or improving or growing:
            if improving:
                reasons.append(f"margin improving ({row['cm3_improving_pp']:+.1f}pp)")
            return "Fix", "; ".join(reasons)
        reasons.append("bottom volume tier, no momentum")
        return "Harvest", "; ".join(reasons)

    # 4. harvest: profitable, below target, small and fading
    if below_target and vol_tier == 3 and not growing:
        reasons.insert(0, f"CM3% {cm3:.1f} below target {target:.1f}, low volume, "
                          "flat/declining")
        return "Harvest", "; ".join(reasons)

    # 5. at/above target
    if target is None or cm3 >= target:
        if stalled:
            reasons.insert(0, "healthy margin but supply stalled")
            return "Fix", "; ".join(reasons)
        if vol_tier == 1 and (declining or not growing):
            reasons.insert(0, "top seller at target margin, momentum "
                              f"{'negative' if declining else 'flat'}")
            return "Defend", "; ".join(reasons)
        if growing or (top_vol and not declining):
            reasons.insert(0, f"CM3% {cm3:.1f} >= target"
                           + (f", sales {trend:+.0f}% 3m" if pd.notna(trend) else ""))
            return "Scale", "; ".join(reasons)

    reasons.insert(0, "between target and broken - no trigger fired")
    return "Watch", "; ".join(reasons)


def apply_overrides(verdicts: pd.DataFrame) -> pd.DataFrame:
    path = OVERRIDES_DIR / "verdict_overrides.csv"
    verdicts["manual"] = False
    if not path.exists():
        return verdicts
    ov = pd.read_csv(path, dtype=str).dropna(subset=["sku", "country", "verdict"])
    for _, o in ov.iterrows():
        mask = (verdicts["sku"] == o["sku"]) & (verdicts["country"] == o["country"])
        verdicts.loc[mask, "verdict"] = o["verdict"]
        verdicts.loc[mask, "reasons"] = "MANUAL: " + str(o.get("note", "") or "")
        verdicts.loc[mask, "manual"] = True
    return verdicts


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

    feat = build_features(facts, launch, review_month, cfg)
    verdict, reasons = zip(*(classify_row(r, cfg, targets) for _, r in feat.iterrows()))
    feat["verdict"] = verdict
    feat["reasons"] = reasons
    feat["review_month"] = review_month
    feat["cm3_target"] = feat["country"].map(lambda c: cm3_target_for(c, targets))
    feat = apply_overrides(feat)

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    feat.to_csv(VERDICTS_PATH, index=False)
    feat.to_csv(HISTORY_DIR / f"verdicts_{review_month}.csv", index=False)

    print(feat["verdict"].value_counts().to_string())
    print(f"-> {VERDICTS_PATH}")
    return feat


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)

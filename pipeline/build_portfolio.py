"""Build the unified monthly SKU x country fact table for the portfolio review.

Sources
-------
1. Nova margin export (novadata_exports/margin_export_*.csv.gz)
   Vanatari International account, daily CM1/2/3 per SKU x Amazon marketplace.
2. wowtamins Amazon DE monthly snapshot (data/source/wowtamins_amazon_de_monthly.csv)
   pulled from the Novadata API (separate seller account, DE only, no PPC data).
3. Vegavero Shopify monthly snapshot (data/source/shopify_vegavero_monthly.csv)
   pulled from the Shopify Admin API; CM is ESTIMATED from COGS + fee assumptions.
   CM3 subtracts actual Google Ads + Meta spend (data/source/*_web_account_*.csv
   and google_ads_web_product_monthly.csv from Windsor.ai; Google product spend
   mapped to SKUs via data/source/shopify_variant_sku_map.csv, the rest pro-rata).
4. Historical Excel workbook (data/source/Vanatari_Product_Insights_v3.xlsx)
   25 months of channel-level net sales / ad spend / ROAS / BSR per SKU. Used for
   launch-date proxies, wowtamins/Jasnum history and drill-down context.

Outputs (data/generated/)
-------------------------
facts_monthly.csv.gz  brand, channel, country, sku, product, month, net_sales,
                      units, ad_spend, cm1..cm3, cm*_pct, cm_source, in_scope
launch_dates.csv      per-SKU global first sale + per SKU x country market entry
excel_history.csv.gz  long-format Excel metrics (net_sales, ad_spend, roas, bsr)
"""
from __future__ import annotations

import gzip
import sys
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.common import (  # noqa: E402
    COGS_CSV,
    EXCEL_PATH,
    EXPORTS_DIR,
    FACTS_PATH,
    GENERATED_DIR,
    GOOGLE_ADS_PRODUCT_CSV,
    LAUNCH_PATH,
    MARKETPLACE_TO_COUNTRY,
    OVERRIDES_DIR,
    SHOPIFY_ASSUMPTIONS_YAML,
    SHOPIFY_COUNTRY,
    SHOPIFY_CSV,
    SOURCE_DIR,
    TAGGING_XLSX,
    VARIANT_SKU_MAP_CSV,
    WEB_ACCOUNT_SPEND_GLOB,
    WOWTAMINS_CSV,
)

EXCEL_START = "2024-07"  # first month in the workbook -> launch dates are censored here
SHOPIFY_LIVE_FROM = "2025-09"  # vegavero.com re-platform: live store data valid from here


# --------------------------------------------------------------------------- #
# 1. Vanatari Amazon: Nova margin export (daily -> monthly per SKU x country)
# --------------------------------------------------------------------------- #
def load_margin_export() -> pd.DataFrame:
    files = sorted(EXPORTS_DIR.glob("margin_export_*.csv.gz"))
    if not files:
        raise FileNotFoundError("No margin_export_*.csv.gz in novadata_exports/")
    path = files[-1]
    print(f"  margin export: {path.name}")
    df = pd.read_csv(
        path,
        usecols=[
            "Period", "Marketplace Name", "SKU", "Product", "Brand",
            "Units", "Product Sales",
            "Contribution Margin 1", "Contribution Margin 2", "Contribution Margin 3",
            "Advertising Costs",
        ],
        compression="gzip",
    )
    df["month"] = df["Period"].str[:7]
    df["country"] = df["Marketplace Name"].map(MARKETPLACE_TO_COUNTRY)
    df = df.dropna(subset=["country", "SKU"])
    grouped = (
        df.groupby(["Brand", "country", "SKU", "month"], as_index=False)
        .agg(
            product=("Product", "first"),
            net_sales=("Product Sales", "sum"),
            units=("Units", "sum"),
            cm1=("Contribution Margin 1", "sum"),
            cm2=("Contribution Margin 2", "sum"),
            cm3=("Contribution Margin 3", "sum"),
            ad_spend=("Advertising Costs", "sum"),
        )
        .rename(columns={"Brand": "brand", "SKU": "sku"})
    )
    grouped["brand"] = grouped["brand"].fillna("Vegavero")
    grouped["channel"] = "Amazon"
    grouped["cm_source"] = "actual"
    grouped["in_scope"] = True
    # Nova stores Advertising Costs as a negative amount (a cost). Store ad_spend
    # as a positive magnitude, consistent with the webshop, so spend can be summed
    # and compared. (CM1/2/3 already net it out, so this only affects ad_spend.)
    grouped["ad_spend"] = grouped["ad_spend"].abs()
    return grouped


# --------------------------------------------------------------------------- #
# 2. wowtamins Amazon DE (Novadata API snapshot, monthly)
# --------------------------------------------------------------------------- #
def load_wowtamins() -> pd.DataFrame:
    df = pd.read_csv(WOWTAMINS_CSV)
    df = df.rename(columns={"month": "month", "sku": "sku"})
    df["brand"] = "wowtamins"
    df["channel"] = "Amazon"
    df["country"] = "DE"
    df["product"] = df["sku"]
    df["ad_spend"] = 0.0  # no PPC data in the wowtamins Nova account (data gap)
    df["cm_source"] = "actual"
    df["in_scope"] = True
    return df[
        ["brand", "channel", "country", "sku", "product", "month",
         "net_sales", "units", "ad_spend", "cm1", "cm2", "cm3", "cm_source", "in_scope"]
    ]


# --------------------------------------------------------------------------- #
# 3. Vegavero Shopify (Shopify API snapshot) with provisional CM from COGS
# --------------------------------------------------------------------------- #
def load_web_ad_spend() -> tuple[pd.DataFrame, pd.Series]:
    """Web ad spend for vegavero.com (Windsor.ai snapshots).

    Returns
    -------
    mapped : per (month, sku) product-attributed spend. Only Google Ads carries
             a product-level feed (Shopping/PMax placements), mapped variant id
             -> SKU via the Shopify Admin API map.
    totals : per-month account-level spend across every platform CSV matching
             WEB_ACCOUNT_SPEND_GLOB (Google Ads + Meta + any future), Vegavero
             accounts only. The gap between totals and product-attributed spend
             (search/brand campaigns, all of Meta, unmapped Merchant Center
             items) is allocated pro-rata by net sales in load_shopify().
    """
    prod = pd.read_csv(GOOGLE_ADS_PRODUCT_CSV, dtype={"product_item_id": str})
    vmap = pd.read_csv(VARIANT_SKU_MAP_CSV, dtype={"variant_id": str})
    prod = prod.merge(
        vmap, left_on="product_item_id", right_on="variant_id", how="left"
    )
    mapped = (
        prod.dropna(subset=["sku"])
        .groupby(["month", "sku"], as_index=False)["spend"]
        .sum()
    )

    # account-level spend, one CSV per platform; webshop = Vegavero accounts only
    # (wowtamins ad accounts are excluded - that store's sales are not connected).
    frames = []
    for path in sorted(SOURCE_DIR.glob(WEB_ACCOUNT_SPEND_GLOB)):
        a = pd.read_csv(path)
        a["platform"] = path.name.split("_web_account")[0]
        frames.append(a)
    if not frames:
        raise FileNotFoundError(f"No {WEB_ACCOUNT_SPEND_GLOB} in {SOURCE_DIR}")
    acct = pd.concat(frames, ignore_index=True)
    acct = acct[acct["account"].astype(str).str.startswith("Vegavero")]
    totals = acct.groupby("month")["spend"].sum()

    by_platform = acct.groupby("platform")["spend"].sum().sort_values(ascending=False)
    mapped_share = (
        prod["spend"][prod["sku"].notna()].sum() / totals.sum() if totals.sum() else float("nan")
    )
    plat = ", ".join(f"{p} €{v:,.0f}" for p, v in by_platform.items())
    print(f"  web ads: €{totals.sum():,.0f} over {totals.index.min()}..{totals.index.max()}"
          f" ({plat}); {mapped_share:.0%} product-attributed via variant map")
    return mapped, totals


def load_shopify() -> pd.DataFrame:
    df = pd.read_csv(SHOPIFY_CSV)
    df = df[df["month"] >= SHOPIFY_LIVE_FROM]
    df = df[df["sku"] != "UNKNOWN"].copy()

    with open(SHOPIFY_ASSUMPTIONS_YAML) as fh:
        cfg = yaml.safe_load(fh)

    cogs = pd.read_csv(COGS_CSV, usecols=["sku", "COGS"])
    cogs = cogs.groupby("sku", as_index=False)["COGS"].median().rename(
        columns={"COGS": "cogs_unit"}
    )
    df = df.merge(cogs, on="sku", how="left")
    df["cogs_known"] = df["cogs_unit"].notna()
    fallback_pct = cfg["fallback_cogs_pct"] / 100.0
    units = df["net_items_sold"].clip(lower=0)
    est_cogs = np.where(
        df["cogs_known"],
        df["cogs_unit"] * units,
        df["net_sales"].clip(lower=0) * fallback_pct,
    )
    payment = df["net_sales"].clip(lower=0) * cfg["payment_fee_pct"] / 100.0 \
        + df["orders"].clip(lower=0) * cfg["payment_fee_per_order"]
    fulfil = df["orders"].clip(lower=0) * cfg["fulfillment_per_order"] \
        + units * cfg["pick_pack_per_item"]

    df["cm1"] = df["net_sales"] - est_cogs
    df["cm2"] = df["cm1"] - payment - fulfil

    # Web marketing: actual Google Ads + Meta spend (Windsor.ai). Google product
    # spend lands on its SKU; the monthly remainder (Google search/brand, all of
    # Meta, spend on SKUs without a sales row) is allocated pro-rata by net sales.
    # CM3 is computed ONLY for months that have ads data (Oct 2025 onward). A
    # month without an ads figure gets ad_spend = CM3 = NaN (treated as "no CM3"
    # downstream) rather than a flattering CM3 = CM2 that hides marketing cost.
    mapped, totals = load_web_ad_spend()
    df = df.merge(
        mapped.rename(columns={"spend": "ads_product"}), on=["month", "sku"], how="left"
    )
    df["ads_product"] = df["ads_product"].fillna(0.0)
    month_total = df["month"].map(totals)
    has_ads = month_total.notna()
    landed = df.groupby("month")["ads_product"].transform("sum")
    residual = (month_total - landed).clip(lower=0)
    pos_sales = df["net_sales"].clip(lower=0)
    month_pos_sales = pos_sales.groupby(df["month"]).transform("sum")
    sales_share = pos_sales / month_pos_sales.where(month_pos_sales > 0)
    df["ad_spend"] = np.where(has_ads, df["ads_product"] + residual * sales_share, np.nan)
    # NaN ad_spend -> NaN CM3: only calculate CM3 where ads data is available.
    df["cm3"] = df["cm2"] - df["ad_spend"]

    out = df.rename(columns={"net_items_sold": "units"}).copy()
    out["brand"] = "Vegavero"
    out["channel"] = "Shopify"
    out["country"] = SHOPIFY_COUNTRY
    out["product"] = out["sku"]
    out["cm_source"] = np.where(out["cogs_known"], "estimated", "estimated-fallback")
    out["in_scope"] = True
    return out[
        ["brand", "channel", "country", "sku", "product", "month",
         "net_sales", "units", "ad_spend", "cm1", "cm2", "cm3", "cm_source", "in_scope"]
    ]


# --------------------------------------------------------------------------- #
# 4. Historical Excel workbook (long format, several metrics)
# --------------------------------------------------------------------------- #
def parse_excel_sheet(wb, sheet: str, metric: str) -> pd.DataFrame:
    ws = wb[sheet]
    rows = list(ws.iter_rows(values_only=True))
    header = None
    for i, row in enumerate(rows):
        if row[0] == "Brand" and row[2] == "Product":
            header = i
            break
    if header is None:
        raise ValueError(f"No header row found in sheet {sheet}")
    months = [str(m) for m in rows[header][3:] if m]
    records = []
    brand = channel = None
    for row in rows[header + 1:]:
        a, b, c = row[0], row[1], row[2]
        if c is None:
            continue
        if a and str(c).startswith("▸"):
            brand, channel = str(a), str(b)
            continue
        if brand is None or b is None:
            continue
        for month, val in zip(months, row[3:]):
            if val is None or val == "—":
                continue
            try:
                val = float(val)
            except (TypeError, ValueError):
                continue
            records.append((brand, channel, str(b), str(c), month, metric, val))
    return pd.DataFrame(
        records,
        columns=["brand", "channel", "sku", "product", "month", "metric", "value"],
    )


def load_excel_history() -> pd.DataFrame:
    wb = openpyxl.load_workbook(EXCEL_PATH, data_only=True, read_only=True)
    frames = [
        parse_excel_sheet(wb, "Net Sales", "net_sales"),
        parse_excel_sheet(wb, "Ad Spend", "ad_spend"),
        parse_excel_sheet(wb, "ROAS", "roas"),
        parse_excel_sheet(wb, "BSR - Display Grp", "bsr"),
    ]
    wb.close()
    return pd.concat(frames, ignore_index=True)


def excel_facts(history: pd.DataFrame) -> pd.DataFrame:
    """Sales-only fact rows for channels not covered by a snapshot source.

    - Jasnum Amazon: whole history (no CM anywhere -> data gap).
    - wowtamins Shopify: whole history (separate store, not connected -> gap).
    - wowtamins Amazon: months BEFORE the Nova snapshot (sales trend context).
    - Vegavero Shopify: months BEFORE the live store cut-over.
    Vanatari (Vegavero et al.) Amazon Excel rows are channel-level (no country),
    so they stay out of the fact table entirely - the margin export covers them.
    """
    sales = history[history["metric"] == "net_sales"]
    keep = []
    for (brand, channel), grp in sales.groupby(["brand", "channel"]):
        if brand == "Jasnum" and channel == "Amazon":
            keep.append(grp.assign(country="DE", cm_source="none", in_scope=True))
        elif brand == "wowtamins" and channel == "Shopify":
            keep.append(
                grp.assign(country=SHOPIFY_COUNTRY, cm_source="none", in_scope=True)
            )
        elif brand == "wowtamins" and channel == "Amazon":
            pre = grp[grp["month"] < "2026-01"]
            keep.append(pre.assign(country="DE", cm_source="none", in_scope=True))
        elif brand == "Vegavero" and channel == "Shopify":
            pre = grp[grp["month"] < SHOPIFY_LIVE_FROM]
            keep.append(
                pre.assign(country=SHOPIFY_COUNTRY, cm_source="none", in_scope=True)
            )
    if not keep:
        return pd.DataFrame()
    df = pd.concat(keep, ignore_index=True)
    df = df.rename(columns={"value": "net_sales"})
    for col in ("units", "ad_spend", "cm1", "cm2", "cm3"):
        df[col] = np.nan
    return df[
        ["brand", "channel", "country", "sku", "product", "month",
         "net_sales", "units", "ad_spend", "cm1", "cm2", "cm3", "cm_source", "in_scope"]
    ]


# --------------------------------------------------------------------------- #
# 5. Launch dates (first-sale proxy, editable overrides)
# --------------------------------------------------------------------------- #
def build_launch_dates(facts: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    sales_hist = history[
        (history["metric"] == "net_sales") & (history["value"] > 0)
    ][["sku", "month"]]
    sales_facts = facts[facts["net_sales"] > 0][["sku", "month"]]
    first = (
        pd.concat([sales_hist, sales_facts], ignore_index=True)
        .groupby("sku", as_index=False)["month"]
        .min()
        .rename(columns={"month": "first_sale_month"})
    )
    first["censored"] = first["first_sale_month"] <= EXCEL_START
    first["source"] = "first-sale proxy"

    override_path = OVERRIDES_DIR / "launch_dates_overrides.csv"
    if override_path.exists():
        ov = pd.read_csv(override_path, dtype=str)
        ov = ov.dropna(subset=["sku", "launch_month"])
        first = first.merge(
            ov[["sku", "launch_month"]], on="sku", how="outer"
        )
        use_ov = first["launch_month"].notna()
        first.loc[use_ov, "first_sale_month"] = first.loc[use_ov, "launch_month"]
        first.loc[use_ov, "censored"] = False
        first.loc[use_ov, "source"] = "manual override"
        first = first.drop(columns=["launch_month"])
    return first


def load_tags() -> pd.DataFrame:
    """Top Seller / New Product flags per country from the Nova custom drawers."""
    try:
        raw = pd.read_excel(TAGGING_XLSX, sheet_name=0, header=1)
    except Exception:
        return pd.DataFrame(columns=["sku", "country", "tag"])
    col_map = {
        "DE filters": "DE", "IT filters": "IT", "ES filters": "ES",
        "FR filters": "FR", "UK filters": "GB",
    }
    records = []
    for _, row in raw.iterrows():
        sku = row.get("SKU")
        if not isinstance(sku, str):
            continue
        for col, country in col_map.items():
            tag = row.get(col)
            if isinstance(tag, str) and tag.strip():
                records.append((sku, country, tag.strip()))
    return pd.DataFrame(records, columns=["sku", "country", "tag"])


# --------------------------------------------------------------------------- #
def main() -> None:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    OVERRIDES_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading sources ...")
    margin = load_margin_export()
    wow = load_wowtamins()
    shop = load_shopify()
    history = load_excel_history()
    ex_facts = excel_facts(history)

    facts = pd.concat([margin, wow, shop, ex_facts], ignore_index=True)
    facts = facts.sort_values(["brand", "channel", "country", "sku", "month"])

    for c in ("cm1", "cm2", "cm3"):
        facts[f"{c}_pct"] = (facts[c] / facts["net_sales"].where(facts["net_sales"] != 0)) * 100

    launch = build_launch_dates(facts, history)
    tags = load_tags()

    facts.to_csv(FACTS_PATH, index=False, compression="gzip")
    launch.to_csv(LAUNCH_PATH, index=False)
    history.to_csv(GENERATED_DIR / "excel_history.csv.gz", index=False, compression="gzip")
    tags.to_csv(GENERATED_DIR / "tags.csv", index=False)

    print(f"facts: {len(facts):,} rows -> {FACTS_PATH}")
    print(f"  by source: {facts['cm_source'].value_counts().to_dict()}")
    print(f"  months: {facts['month'].min()} .. {facts['month'].max()}")
    print(f"launch dates: {len(launch):,} SKUs ({int(launch['censored'].sum())} censored)")
    print(f"excel history: {len(history):,} rows | tags: {len(tags):,}")


if __name__ == "__main__":
    main()

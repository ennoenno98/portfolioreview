"""Shared constants, paths and helpers for the portfolio review pipeline."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "data" / "source"
GENERATED_DIR = REPO_ROOT / "data" / "generated"
OVERRIDES_DIR = REPO_ROOT / "data" / "overrides"
CONFIG_DIR = REPO_ROOT / "config"
EXPORTS_DIR = REPO_ROOT / "novadata_exports"

EXCEL_PATH = SOURCE_DIR / "Vanatari_Product_Insights_v3.xlsx"
WOWTAMINS_CSV = SOURCE_DIR / "wowtamins_amazon_de_monthly.csv"
SHOPIFY_CSV = SOURCE_DIR / "shopify_vegavero_monthly.csv"
COGS_CSV = SOURCE_DIR / "pricer_products_cogs.csv"
GOOGLE_ADS_PRODUCT_CSV = SOURCE_DIR / "google_ads_web_product_monthly.csv"
GOOGLE_ADS_ACCOUNT_CSV = SOURCE_DIR / "google_ads_web_account_monthly.csv"
VARIANT_SKU_MAP_CSV = SOURCE_DIR / "shopify_variant_sku_map.csv"
TAGGING_XLSX = SOURCE_DIR / "product_tagging.xlsx"
TARGETS_JSON = CONFIG_DIR / "ap26_targets.json"
RULES_YAML = CONFIG_DIR / "rules.yaml"
SHOPIFY_ASSUMPTIONS_YAML = CONFIG_DIR / "shopify_assumptions.yaml"

FACTS_PATH = GENERATED_DIR / "facts_monthly.csv.gz"
LAUNCH_PATH = GENERATED_DIR / "launch_dates.csv"
VERDICTS_PATH = GENERATED_DIR / "verdicts.csv"              # channel grain (primary)
COUNTRY_VERDICTS_PATH = GENERATED_DIR / "verdicts_country.csv"  # per marketplace
HISTORY_DIR = GENERATED_DIR / "verdict_history"

# Nova margin export: amazon.xx -> ISO-ish country code used across the app
MARKETPLACE_TO_COUNTRY = {
    "amazon.de": "DE",
    "amazon.fr": "FR",
    "amazon.it": "IT",
    "amazon.es": "ES",
    "amazon.co.uk": "GB",
    "amazon.nl": "NL",
    "amazon.ie": "IE",
    "amazon.se": "SE",
    "amazon.pl": "PL",
    "amazon.com.be": "BE",
}

# Shopify D2C is treated as one "market" in the SKU x country review grid.
SHOPIFY_COUNTRY = "WEB"

VERDICT_ORDER = [
    "Incubate",
    "Exit",
    "Fix",
    "Harvest",
    "Scale",
    "Defend",
    "Watch",
    "No data",
]


def load_targets() -> dict:
    """AP26 plan targets (fractions, e.g. 0.196898). Keys per country code."""
    with open(TARGETS_JSON) as fh:
        return json.load(fh)


def cm3_target_for(country: str, targets: dict) -> float | None:
    """CM3 target (in percent, e.g. 19.7) for a country; portfolio mean as fallback."""
    table = targets.get("cm3_target", {})
    if country in table:
        return table[country] * 100.0
    if table:
        return sum(table.values()) / len(table) * 100.0
    return None


def month_range(last: str, n: int) -> list[str]:
    """The n months ending at `last` (inclusive), as YYYY-MM strings."""
    end = pd.Period(last, freq="M")
    return [str(end - i) for i in range(n - 1, -1, -1)]


def safe_pct(num: pd.Series, den: pd.Series) -> pd.Series:
    """num/den*100 with 0-denominator -> NaN."""
    out = num.divide(den.where(den != 0)) * 100.0
    return out

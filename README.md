# Vanatari Portfolio Review

The portfolio management operating system: one place where every SKU × market
gets a monthly verdict — **Scale · Defend · Watch · Incubate · Fix · Harvest ·
Exit** — from explicit, versioned rules anchored on CM1/2/3 %, the AP26 plan
targets, sales momentum and product age.

```
Data sources                Pipeline                       Outputs
────────────────           ─────────────────────          ──────────────────────
Nova margin export    ──►  build_portfolio.py   ──►       facts_monthly.csv.gz
wowtamins Nova pull   ──►  (unified monthly           ┌── launch_dates.csv
Shopify sales pull    ──►   SKU × market facts)       │
Historical workbook   ──►                             ▼
AP26 targets               classify.py (rules.yaml) ──►  verdicts.csv + history
productpricer COGS                                        │
                           build_review_pack.py     ──►   reports/packs/review_YYYY-MM.md
                           app.py (Streamlit)       ──►   interactive review dashboard
```

## Run the dashboard

Locally:

```bash
pip install -r requirements.txt
streamlit run app.py
```

The app is self-contained: it reads only the committed files in
`data/generated/` and `config/`, makes no live API calls, and writes nothing.
That is what makes it safe to host — the data-refresh (Nova/Shopify/Windsor
pulls) happens offline in the pipeline; the deployed app just renders the
committed snapshot.

## Deploy to Streamlit Community Cloud

Push-to-deploy — every push to the tracked branch redeploys automatically.

1. Push this repo to GitHub (done — branch `claude/new-session-nzuzts`).
2. Go to <https://share.streamlit.io> and sign in with the GitHub account that
   can see `ennoenno98/portfolioreview`.
3. **Create app → Deploy a public app from GitHub** and set:
   - **Repository**: `ennoenno98/portfolioreview`
   - **Branch**: `main` (after the PRs merge) or `claude/new-session-nzuzts`
     to preview the current work immediately
   - **Main file path**: `app.py`
   - Python version: 3.11 (Advanced settings) — matches the pipeline.
4. **Deploy**. First build installs `requirements.txt` (~1–2 min); afterwards
   every `git push` to that branch redeploys within seconds.

No secrets or environment variables are needed — the app ships with its data.
If you later want to keep the review private, use *Deploy a private app* instead
and share it with named viewers (free tier allows a limited number).

## Monthly cadence

1. **Refresh data** (first business days of the month)
   - Drop the newest `margin_export_*.csv.gz` into `novadata_exports/`
     (same Nova export as Margin-Analytics uses).
   - Refresh `data/source/wowtamins_amazon_de_monthly.csv` (Novadata, wowtamins
     account) and `data/source/shopify_vegavero_monthly.csv` (Shopify analytics,
     net sales by variant SKU by month).
2. **Run the pipeline**
   ```bash
   python pipeline/build_portfolio.py
   python pipeline/classify.py            # defaults to last complete month
   python reports/build_review_pack.py
   ```
3. **Hold the review** with the generated pack + dashboard
   (`streamlit run app.py`). Decisions that differ from the engine go into
   `data/overrides/verdict_overrides.csv` with a note and owner — the override
   wins and is marked *manual* everywhere.
4. **Commit** the generated files. `data/generated/verdict_history/` is the
   audit trail; next month's pack diffs against it automatically.

## The rules (config/rules.yaml)

Deterministic and ordered — the first match wins. Current calibration
(agreed Jul 2026):

| Verdict | Trigger | Action owner question |
|---|---|---|
| **Incubate** | ≤ 6 months since first sale | Is it on track to reach target CM3% by month 6? |
| **Exit** | CM3 < 0 in ≥ 3 of last 6 months, 6m window negative overall, not improving, out of incubation | Delist / liquidate in this market. Top-volume losers get one Fix cycle first ("EXIT CANDIDATE"). |
| **Fix** | CM3% > 5pp below country target (or negative), but top volume / improving / growing — also healthy SKUs with stalled supply | What repairs it: price, fees, COGS, ad efficiency, restock? |
| **Harvest** | Profitable but below target, bottom volume tier, no momentum | Cut ads to maintenance, no aggressive restock. |
| **Scale** | ≥ CM3% target and (growing ≥ 10% or top-tier volume, not declining) | Budget, inventory, variations, next market. |
| **Defend** | ≥ target, top volume tier, momentum flat/negative | Availability, price position, listing quality. |
| **Watch** | Everything in between | Nothing this month. |
| **No data** | < 3 months history or no margin data | Close the data gap (see below). |

Margin gates use **absolute AP26 CM3 targets per country** (DE 19.7 · IT 16.6
· FR 23.6 · ES 19.6 · GB 27.4 · IE 29.4 · NL 34.0); at channel level they are
blended per SKU, weighted by each country's TTM net sales. Priority within a
verdict uses the **relative margin × volume tiers** carried over from
Margin-Analytics (tier 1 = top third of its group), visualised as the quadrant
map in the app.

## Review unit

**Headline: SKU × channel** — each SKU gets one verdict per channel (Amazon vs
Shopify/`WEB`), with the Amazon marketplaces collapsed together and the CM3
target blended by country sales mix. The channel row shows a compact per-country
verdict summary (`DE·Fix FR·Scale IT·Watch`) so the country split is visible
without the detail.

**Underneath: SKU × country** — every marketplace is still classified on its own
country target (a SKU can Scale in DE and Exit in FR) and written to
`data/generated/verdicts_country.csv`. The app's **By country** tab shows the
full per-marketplace table and drill-down.

## Release dates

No system carries real launch dates, so the pipeline derives
**first-sale-month proxies** (25 months of history). SKUs already selling in
Jul 2024 are *censored* — age unknown, incubation never applies to them.
Correct any SKU in `data/overrides/launch_dates_overrides.csv`
(`sku,launch_month,note`); overrides beat the proxy.

## Known data gaps (also in the app's *Data gaps* tab)

| Gap | Impact | How to close |
|---|---|---|
| Jasnum: no margin feed | whole brand is "No data" | connect the account to Nova or provide a margin export |
| wowtamins webshop not connected | ~€0.9m TTM unclassifiable | authorize the wowtamins Shopify store, add a snapshot like the Vegavero one |
| Webshop margins estimated | CM for `WEB` uses COGS + placeholder fees | put real payment/3PL numbers into `config/shopify_assumptions.yaml` |
| Webshop marketing = Google Ads only | CM3 for `WEB` is computed **only for months with Google Ads data** (Windsor.ai, from Oct 2025); Shopping/PMax spend lands on its SKU, the search/brand remainder is allocated pro-rata by net sales. Months without ads data have no CM3 (not CM3 = CM2). | connect Meta (and other channels) in Windsor.ai and extend `load_web_ad_spend()` |
| wowtamins Amazon: no PPC data, June 2025 missing | CM2 = CM3, ad checks impossible | check the Nova account's advertising connection |
| 209 of 322 launch dates censored | incubation blind for older SKUs | fill `launch_dates_overrides.csv` from the ERP |
| Amazon CM history starts Jul 2025 | pre-2025 margin trends unavailable | fine for the monthly cadence; older data is sales-only |

## Repository layout

```
app.py                      Streamlit dashboard (Vanatari CD 2026)
pipeline/build_portfolio.py sources -> data/generated/facts_monthly.csv.gz
pipeline/classify.py        rules engine -> verdicts.csv (SKU x channel) +
                            verdicts_country.csv (SKU x country) + verdict_history/
reports/build_review_pack.py monthly markdown pack -> reports/packs/
config/rules.yaml           thresholds + verdict definitions (edit me)
config/ap26_targets.json    plan targets (from productpricer)
config/shopify_assumptions.yaml  webshop fee placeholders (edit me)
data/source/                input snapshots (Excel, API pulls, COGS)
data/overrides/             manual truth: launch dates + verdict overrides
data/generated/             pipeline outputs (committed for auditability)
novadata_exports/           raw Nova margin/product exports (like Margin-Analytics)
```

Design follows the Vanatari Corporate Design 2026 (warm orange `#ff5c3e`,
light beige `#fbf7f2`, dark plum `#3c1826`), consistent with the Nova-Analytics
and productpricer apps. Chart palettes are colorblind-validated; verdict colors
are always paired with text labels.

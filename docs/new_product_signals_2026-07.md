# New-product signals — what predicts an early winner?

Follow-up to the launch-ramp analysis. Question: **in the first few months after
launch, which signals/KPIs identify the products that will become winners?**
And how much of that is real correlation vs. wishful causation?

**Cohort:** 37 products with a non-censored launch date and ≥12 months of history
(so we see them from launch through a stable run-rate). **Outcome ("winner"):**
mature run-rate = average monthly net sales in months 9–12. Small sample — read
everything below as directional.

## Finding 1 — early signals are weak predictors in the data we have today

Rank correlation of each early signal (first 0–3 months) with the mature run-rate:

| Early signal (months 0–3) | Rank corr. with mature run-rate |
|---|---|
| **Early revenue** (cumulative €) | **+0.33** |
| Early BSR, best rank *(n=9 only)* | +0.32 |
| Month-3 sales level | +0.18 |
| Early growth / momentum (m3 ÷ m1) | +0.05 |
| Early units sold | −0.06 |
| Market breadth (# markets by m3) | −0.12 |

Only **early revenue** carries a meaningful positive signal, and it is modest
(0.33). Translated into a decision: a product in the **top third by early revenue
has a 38% chance** of ending up a top-third winner — barely above the **35% base
rate**. Early performance is a hint, not a verdict.

Notable nulls:
- **Units don't predict; revenue does.** Selling lots of cheap units early doesn't
  translate into a big product — price/value mix matters more than volume.
- **Momentum doesn't predict.** A fast month-3-vs-month-1 jump is mostly noise off
  a tiny base.
- **Breadth is slightly *negative*.** Launching into many markets early does *not*
  help — if anything it spreads effort thin. Prefer proving one market first.
- **BSR looks promising (0.32)** but only 9 of the cohort had BSR data in their
  first 3 months, so treat it as a lead to watch, not a conclusion.

## Finding 2 — patience beats precision: the read sharpens with time

Correlation of cumulative sales-through-month-N with the mature run-rate:

| Judge at… | m1 | m2 | m3 | m4 | m5 | m6 | m7 |
|---|---|---|---|---|---|---|---|
| Rank corr. | 0.33 | 0.34 | 0.33 | 0.39 | 0.34 | 0.42 | **0.53** |

The signal is flat-to-weak for the first ~3 months, then climbs — crossing into
"reasonably informative" around **month 6–7**. This lines up with the ramp
analysis (products reach ~half their run-rate by month 6). **Judging before ~6
months mostly measures noise.** The 6-month incubation shield is well placed.

## Correlation vs. causation — be careful here

Everything above is **correlation on observational data — none of it is causal.**
Two cautions that matter for how you act on it:

1. **Early revenue almost certainly isn't a lever.** It's an early *sample of the
   same underlying demand* that produces the mature run-rate — not something you
   can "do more of" to cause success. Pushing early sales (e.g. via discounts)
   could even correlate with *worse* economics.
2. **The signals that *would* be causal levers aren't in this dataset early
   enough** to test: ad support (Amazon PPC spend only starts Feb 2026), price /
   promotion, review velocity and rating, listing/content quality, and organic
   rank trajectory. We can't yet separate "product would have won anyway" from
   "we made it win."

Establishing causation needs a **controlled test**, not more back-looking
regression: e.g. a structured launch playbook where new SKUs get a defined PPC
budget and review-generation push, with a hold-out, tracked against outcome.

## What to start tracking to actually spot early winners

The current data can only see sales and (patchy) BSR. To get *leading* indicators
that fire before month 6, capture these from launch — the connectors already exist:

- **PPC efficiency from day one** — ROAS / TACOS per SKU (Amazon Ads + Google/Meta
  via Windsor). A new product that converts ad spend efficiently early is the
  strongest *causal* candidate signal.
- **Review velocity & average rating** — reviews-per-week and star trend; the
  classic Amazon flywheel input.
- **In-category BSR trajectory** (not level) — is the rank *improving* week over
  week within its own category.
- **Conversion rate & sessions** (Amazon business reports / GA4 for the webshop).
- **Repeat-purchase rate** (webshop) — early retention separates fads from staples.

## Bottom line

- Today's best early read is **cumulative revenue**, and it's weak until ~month 6.
- **Don't over-react to early units, momentum, or a multi-market launch** — none
  predict success; breadth may hurt.
- The real early-winner signals (ad efficiency, reviews, conversion, rank
  momentum) need to be **connected and tracked going forward**; then we can move
  from correlation to a tested launch playbook.

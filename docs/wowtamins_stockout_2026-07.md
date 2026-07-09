# wowtamins Amazon DE — stock-out investigation (2026-07-09)

Follow-up on the operational alert raised in the June 2026 review pack
("account-wide supply collapse May–June 2026"). Data: Novadata API,
wowtamins GmbH seller account (`A1IC0V7S8XUCL0`), pulled 2026-07-09.

## Finding: full account stock-out, no replenishment inbound

**Weekly net sales (Novadata):**

| Weeks | Net sales / week |
|---|---|
| Mar 16 – May 3 | €22k – €33k (normal run-rate, ~€28k avg) |
| May 4 – May 10 | €6.1k (collapse begins) |
| May 11 – Jul 8 | ≈ €0 (9 consecutive weeks; some weeks net-negative from returns) |

**FBA inventory (snapshot 2026-07-09):** only 9 SKUs carry any FBA inventory
at all; available units are 0–11 per SKU (mostly 0, the rest reserved), and
**incoming stock is 0 on every SKU**. Sales velocity is ~0 across the account.

An earlier mini-gap in March (week of Mar 9 net-negative, followed by a €33k
restock spike on Mar 16) shows the same pattern: this account runs with no
safety stock.

## Impact

- ~9 weeks × ~€28k ≈ **€250k net sales lost so far**, growing ~€28k/week.
- The account keeps booking storage/misc fees against zero sales, so the
  review shows CM3-negative months that are supply-driven, not demand- or
  margin-driven. Verdicts for wowtamins DE SKUs are flagged "supply stalled"
  in the app — do not act on their margins until availability is restored.
- Amazon organic rank for these ASINs erodes the longer the stock-out lasts;
  recovery cost (PPC push, deals) should be budgeted into the restock plan.

## Recommended actions (owner: wowtamins ops)

1. Confirm root cause: production, inbound shipment, or account/listing
   health (the sales stop is too synchronized across all SKUs for organic
   demand loss — check for stranded inventory / listing suppression too).
2. Get a replenishment PO/inbound shipment created — Novadata shows zero
   units incoming, so as of today there is no recovery date at all.
3. Once stock lands, treat the affected SKUs as re-launches (deal/PPC push),
   and only then re-read their portfolio verdicts.

## Data notes for the review pipeline

- June and July 2026 wowtamins rows in `facts_monthly.csv.gz` are near-zero
  by supply, not demand — the classifier's "supply stalled" guard covers this.
- Next monthly refresh should re-check this file's status and delete it once
  the account is back in stock and verdicts normalize.

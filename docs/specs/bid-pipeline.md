# Bid pipeline on favourites

Status: slice 1 built 2026-09-23 (branch `feat/bid-pipeline-status`).
Origin: the Altura comparison — they ask customers how a tender ended; we can
read it from the award ledger.

## Why

1. A customer's own "what are we bidding on" list, on the page they already use.
2. **Win/loss data.** `fit.py`'s weights are argued, not fitted, because
   nothing records which tenders a customer went for and how it ended. This
   feature is how that data starts to exist.

## Slice 1 (built)

- A favourite carries a stage: `bidding` → `submitted` → `won` / `lost`, or
  `no_bid`. NULL = just a bookmark. Plus an optional note (≤300 chars).
- Stored as columns on `proc.user_favorite_act`
  (migration `20260923180000_favorite_bid_stage.sql`). Removing the star, on
  the web or the phone, removes the stage too.
- `/account/favorites`: a stage panel under each card (saves on change), stage
  filter tabs (`?stage=`), and a headline: submitted / won / not won, and the
  win rate over decided tenders.
- **Ledger detection** (`bid_pipeline.detect_outcomes`): for favourites at
  `bidding`/`submitted`, walk the lifecycle chain forward to non-cancelled
  auction/contract acts and read the winners from `act_operator`. If a winner is
  one of the customer's own identities (`fit.operator_ids_for`, i.e. an ΑΦΜ
  an admin linked), then "the award names you"; otherwise "awarded to X".
  Measured locally: about 66% of H1-2025 notices link to an award, 63% with a
  named winner, and about 9% of those have several winners (lots).
- Detection **never writes**. A confirm button records it, and the server
  re-detects rather than reading the form. "Awarded to others" can only be
  confirmed as a loss when we know the customer's ΑΦΜ.
- Entitled users only for detection (winner names are act data). A lapsed
  customer keeps and edits their stages.
- `no_bid` favourites leave the calendar feed, including when a ticked saved
  search matches them.

## Why a suggestion, not automatic

Lots (the award may be for a different lot); joint ventures bid under their
own ΑΦΜ; the ledger records winners only, so it cannot tell "lost" from
"never bid".

## Next slices (not built)

- Stage control on the act page itself, next to the star.
- CRM card: the customer's pipeline and win rate, for admins.
- Feed confirmed outcomes (`bid_stage_source = 'ledger'` first) into
  `fit.py` calibration once there is enough of them.
- Reason codes for `no_bid` / `lost` (currently free text) if the notes show
  a small set of recurring reasons.
- Mobile API: expose the stage fields.

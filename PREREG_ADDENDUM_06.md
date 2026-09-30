# PREREG addendum 06: 2026 scorecard rules, result entry, and draws in the freeze packages

**Date:** 2026-09-30 (UTC). Written **before the first-round freeze and before any 2026 result exists.** No model,
data, selection or baseline rule changes. This file fixes how the frozen 2026 forecasts are scored, so that the
scoring code is committed and pushed before the outcome is known. PREREG §8 (metrics), §11 (freeze and scoring),
§12 (L1-L3) and Addendum 04 §5 (who-was-elected metrics) stay binding; where this file is more specific, it decides.

## 1. The freeze packages also hold the draws

- `outputs/freeze/draws.npz` (first round): the election-day draws of F, E and E0, plus the calibrated probabilistic
  conversions of B, C and D where they exist, as float64 arrays with the category order.
- `outputs/freeze_runoff/draws.npz` (runoff): the same for F, E, E0, B and C.
- Both are hashed in `forecast_hash.txt` with the other files.
- **Why.** Interval coverage and the registered Monte Carlo smoothing `(count + 0.5) / (N + 1)` must be computed from
  the draws behind the published summaries. The summaries store neither the draw count nor the event counts.

## 2. Result entry (evaluation only)

- **File.** `data/manual/results_2026.csv`, with the columns of `data/manual/results_secondary.csv`. No forecast
  script reads it; `tests/test_leakage.py` enforces this.
- **Source.** The TSE official count, read on the TSE results site once 100% of polling sections are totalled
  ("seções totalizadas"). Candidate votes, blank, null and the valid total are typed as displayed. The page is
  archived and a screenshot is hashed. If a plain request to TSE's published results data succeeds, the raw response
  is saved with a provenance record. A refused request is never worked around; the browser reading is then the
  source.
- **Checks before scoring.**
  - Every registered 2026 ballot candidate (`data/manual/ballot_2026.csv`) has exactly one row per round.
  - No other name appears.
  - Candidate votes sum exactly to the valid total. Shares are recomputed from votes.
- **Votes TSE does not count as valid** (for example "anulados sub judice") stay out of the valid total, as TSE
  counts them, and the notes column says so.
- **Later revisions.** If TSE's open-data totals, downloaded manually, differ from the count used, the scorecard is
  recomputed and labelled as a revision. The first scorecard is kept unchanged beside it.

## 3. First-round scorecard (`scripts/score_2026.py --stage r1`)

- **Package check.** Nothing is scored unless `outputs/freeze/forecast_hash.txt` verifies: no listed file missing
  or changed, and no unlisted file.
- **Categories.** The frozen forecast categories: named candidates by name, and Others = 100 minus the named shares
  (as in the backtest).
- **Models F, E, E0 and the conversions of B, C, D.** Scored with `brfc.scoring.score` on the frozen draws, the
  function the backtest used. It gives share MAE, signed and absolute top-two margin error, 80%/94% coverage,
  first-place Brier and log score, and the "leader exceeds 50% of valid votes" Brier.
- **Baseline A (PollingData).** Point only. It is scored on the categories PollingData displayed:
  - named candidates, matched by name;
  - Others, only if PollingData displayed every ballot candidate outside the named set (A's Others is then their
    sum).
  - For the L1 comparison with A, F's MAE is recomputed on the same categories. Both values are reported.
  - If no PollingData reading exists, A is N/A and L1 is assessed against B only, and the scorecard says so.
- **Criteria.**
  - L1: F's MAE ≤ A's (same categories) and ≤ B's (all categories).
  - L2: F's absolute top-two margin error ≤ B's.
  - L3: the number of categories inside F's 94% and 80% intervals, out of the number of categories.
- **Benchmarks, for the events they price.** Market prices and displayed probabilities are not ground truth and not
  model forecasts. Polymarket is scored as follows; PollingData probabilities are scored only if PollingData
  displayed them, with the same rules.
  - First place (event 943054) and election winner (45915): the yes prices of every market of the event that has a
    price are normalised to sum to 1. Outcomes that are not named forecast candidates are pooled in one "other"
    bucket. Scores: Brier over the named candidates plus "other", log score with the floor 1e-4, and P(actual).
    For displayed PollingData probabilities, a total below 100 leaves the remainder in "other"; a total above 100
    by rounding is rescaled to 100.
  - Outright first-round win (45924): the Brier score of the yes price against "the leader exceeds 50% of valid
    votes".
  - The model comparators are F's smoothed first-place probabilities and its smoothed first-round-win probability.

## 4. Who-was-elected scorecard (`--stage president`)

- **When.** After the first round if a candidate is elected outright; otherwise after the runoff count.
- **Forecast scored.** `outputs/freeze/president.json`: the primary F×E combination and the alternatives F×F, E×E
  and F×E0.
- **Scores (Addendum 04 §5).** Brier over every candidate with positive probability plus the "unmodelled" bucket
  (outcome 0), log score `log(max(P(actual), 1e-4))`, and P(actual). The probabilities are exact path frequencies,
  not smoothed (Addendum 05 §3).
- **Benchmark.** The Polymarket election-winner prices at the first-round freeze, under the §3 bucket rule.

## 5. Runoff scorecard (`--stage runoff`, only if a runoff is held)

- **Package check** as in §3, on `outputs/freeze_runoff/`.
- **Models F (primary), E, E0 and the conversions of B and C.** Scored with `brfc.scoring.score` (round 2) on the
  frozen draws. It gives runoff share MAE, margin error, 80%/94% coverage, and winner Brier and log score with the
  registered smoothing.
- **Benchmarks.** The Polymarket election-winner prices at the runoff freeze, and PollingData probabilities if
  displayed, under the §3 rules.

## 6. Outputs and publication

- Each stage writes `outputs/scorecard_2026_<stage>.json`, a per-model CSV and, for the share forecasts, a
  per-category CSV. `docs/scorecard-2026.md` is generated from them.
- The scorecard is published whatever the result. No frozen file is rewritten. The post-mortem keeps pre-election
  information apart from post-election analysis.
- The scoring code is tested on synthetic results only. No 2026 result is entered before the TSE count.

# PREREG addendum 04: conditional "who will be elected" forecast and the runoff forecast

**Date:** 2026-09-29 (UTC). Written **before any head-to-head (runoff) poll was fitted, before any pre-first-round
runoff backtest score was computed, and before any 2026 runoff poll was fitted.** The first-round historical
backtest had already been scored, so the one first-round data revision below (section 3) is a **post-result
revision** and is labelled as such.

## 1. Scope change and rationale
The registered scope was the 2026 first round only, a restriction that came from the original brief. The question
people ask is who will be elected. The scope now adds two forecasts:

- **A. Conditional forecast of who is elected**, frozen together with the first-round forecast (Saturday 2026-10-03,
  22:00 BRT = 2026-10-04T01:00:00Z, the same information cutoff). It combines:
  - P(outright first-round win);
  - P(each pair advances), from the first-round model;
  - P(win | pair), from head-to-head runoff polls fielded before the first round.
- **B. Post-first-round runoff forecast**, frozen Saturday 2026-10-24, 22:00 BRT (2026-10-25T01:00:00Z), with
  information cutoff 2026-10-24. It uses the registered round-2 design (PREREG section 2 round-2 rule, section 4
  model, section 5 round-2 term with model F primary, section 6 horizons). The first-round result and the
  advancing pair are known information at that point.

Both are scored after 2026-10-25 and published whatever the result. Every output carries the label
**"pre-registered; validated on 3 elections only"**, and every probability is worded as "probability of being
elected under this model".

## 2. Data
- **2026 head-to-heads.** Runoff tables from the same pinned Wikipedia PT/EN revisions as the first round,
  refreshed with them at the freeze. Output: `data/interim/polls_wiki_2026_runoff.csv`, with conflicts recorded in
  `conflicts_wiki_2026_runoff.csv` under the same conflict rule.
- **2018 and 2022.** Pre-first-round pairings already parsed in `polls_wiki_{2018,2022}.csv` (round-2 rows with
  fieldwork ending before the first round). Pairs with polls in the 60 days before the first round:

  | Election | Pair | Polls |
  | --- | --- | --- |
  | 2018 | Haddad–Bolsonaro | 45 |
  | 2018 | Ciro–Bolsonaro | 44 |
  | 2018 | Alckmin–Bolsonaro | 44 |
  | 2018 | Bolsonaro–Marina | 33 |
  | 2022 | Bolsonaro–Lula | 65 |
  | 2022 | Ciro–Bolsonaro | 23 |
  | 2022 | Ciro–Lula | 22 |

  Rows without a sample size are excluded by the loader.
- **2014.** No pinned Wikipedia revision lists pre-first-round head-to-heads with sample sizes, so they were
  researched from original releases and contracting-outlet articles:
  - Sources: Datafolha PDFs, Ibope reports, CNT/MDA reports, Vox Populi and Sensus articles, some via the Wayback
    Machine.
  - Output: `data/manual/research_2014/`; 30 polls.
  - Independent verification: every poll was re-read, and all 271 checks were confirmed with 0 conflicts.
  - `scripts/build_2014_supplement.py` builds `data/interim/polls_2014_supplement.csv`. It excludes one Sensus wave
    (basis undeterminable) and rows with a printed 0%.
  - Head-to-head polls fielded 2014-08-20 to 2014-10-04 after exclusions:

    | Pairing | Polls (Datafolha / Ibope / MDA / Vox Populi / Sensus) |
    | --- | --- |
    | Dilma–Aécio | 29 (8/8/5/6/2) |
    | Dilma–Marina | 29 (8/8/5/6/2) |
    | Aécio–Marina | 14 (3/6/5/0/0) |

  - **Inclusion rule (fixed before counting):** a 2014 backtest cell is fitted only with at least 8 polls at its
    cutoff (`config.MIN_POLLS_PER_FIT`). After the dependence rule, Dilma–Aécio has 28 polls at the eve cutoff and
    18 at T-7, so 2014 qualifies.
- **Unverified leads.** Values pasted into the working session from an external tool were treated as unverified
  leads. Only values read by an agent on the cited source were used; 15 of 16 checkable leads matched, and one
  differed from the release.

## 3. Post-result revision of the 2014 first round (labelled; does not replace registered results)
The same research found sample sizes for 11 late 2014 first-round polls missing from the pinned Wikipedia
revision.

- **Why this is a revision.** Their addition was decided **after** the registered historical first-round scores had
  been computed and seen.
- **The registered results stay as they are.** The enriched 2014 first round is reported beside them, labelled
  "post-result data revision". It never feeds the headline, the election-day term used for 2026, or any selection
  rule.
- **Loading.** The rows are tagged `revision_2014_r1` and load only with `load_polls(..., revision_2014=True)`.

## 4. Model for the conditional forecast
- **Head-to-head random walk, one per pairing.** It uses the registered model (PREREG section 4) on the valid share
  of the alphabetically first candidate of the pair, with:
  - the production volatility variant (single-regime, per the registered rule);
  - window start = first-round day − 60 days;
  - information cutoff = the first-round freeze (backtest: first-round eve; secondary cutoff first-round T-7);
  - the walk projected to runoff day, a horizon of about 22 to 28 days;
  - valid-vote conversion, information-time filter and dependence rule as registered;
  - retries per PREREG section 4 and Addendum 03.
- **Which pairs are fitted.** Every pair of ballot candidates with at least 8 head-to-head polls at the cutoff.
  Pairs are chosen from information available at the cutoff, never from the first-round result.
- **Election-day term for this horizon.**
  - Deviation = runoff official valid share of the candidate ranked first in the pair's forecast, minus that
    forecast's mean on runoff day. It uses the eve-cutoff head-to-head fit of the pair that actually advanced.
  - It is estimated leave-one-election-out (target 2014: 2018+2022; target 2018: 2014+2022; target 2022:
    2014+2018; 2026: all three), with the registered conjugate error model (PREREG section 5, priors unchanged).
  - **Primary variant: E (zero mean).** With three elections a mean term cannot be distinguished from zero, and the
    first-round consolidation rationale behind F does not apply to two-candidate contests. F and E0 are reported
    beside E.
- **Pair probabilities.** They come from first-round model F draws at the same cutoff.
  - In each draw, an outright win is a named candidate above 50% of valid votes. Otherwise the pair is the two named
    candidates with the largest shares; "Others" is never a candidate.
  - `P(X elected) = P(X outright) + Σ_pairs P(pair and no outright) × P(X wins | pair)`, where P(X wins | pair) is
    the share of head-to-head draws in which X exceeds 50%.
  - **Independence assumption:** first-round draws and head-to-head draws are treated as independent.
  - **Pairs without a head-to-head fit:** their probability mass is reported as **"unmodelled"** and never
    reallocated.
  - The primary combination is first-round F × head-to-head E; F×F and E×E are also reported.

## 5. Backtest of the conditional forecast
- **Coverage.** Elections 2014, 2018 and 2022, at cutoffs first-round eve (primary) and first-round T-7.
- **Head-to-head scores**, for the pair that actually advanced: runoff share MAE, margin error, and 80%/94%
  equal-tailed interval coverage.
- **"Who was elected" scores:**
  - multi-category Brier over all candidates with positive probability plus the "unmodelled" bucket, where the
    bucket's outcome is 0;
  - log score `log(max(P(actual winner), 1e-4))`;
  - P(actual winner).
- **Baselines, each end to end with the calibrated probabilistic conversion** (leave-one-election-out RMSE, same
  cutoff):
  - B (latest poll per pollster, 14 days) and C (final Datafolha);
  - pair probabilities come from each baseline's first-round conversion;
  - P(win | pair) comes from its head-to-head point and its own head-to-head RMSE.
- **Criteria (reported as met / not met; three elections is very weak evidence):**
  - HR1: head-to-head model E share MAE ≤ baseline B at the eve cutoff.
  - HR2: the actual runoff share lies inside E's 94% interval in at least 2 of 3 elections.
  - HR3: the "who was elected" Brier of F×E ≤ baseline B's.
- **Sensitivity.** Error-model scale priors 1.5 and 6.

## 6. Benchmarks at the freeze (same timestamp as the forecasts)
- **Polymarket winner market** (event 45915): market IDs, exact questions, prices, best bid/ask, volume, UTC
  timestamp and archive URL. Also the first-round markets already registered (events 943054 and 45924).
- **PollingData's presidential forecast section:** its displayed probabilities are recorded only if it publishes
  them, read manually from the page. Nothing is computed on its behalf.

## 7. Schedule
- The first-round freeze time does not move.
- If the conditional forecast is not validated by then, the first-round package is frozen first, and the
  conditional forecast is published as PRELIMINARY with its own timestamp. Validation is not cut to meet the
  deadline.

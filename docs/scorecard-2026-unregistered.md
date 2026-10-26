# 2026 scorecard: UNREGISTERED scoring of the PRELIMINARY forecast

> **Read this first.** The registered scorecard (PREREG s.11, Addendum 06) scores the forecast frozen on 2026-10-03 22:00 BRT. That freeze did not happen. This page scores the last forecast that was public before the first round instead: the PRELIMINARY run with information cutoff 2026-09-29, committed 2026-09-29 and pushed 2026-09-30. Nothing in those files was changed. Every number below is UNREGISTERED and comes from `scripts/score_2026_unregistered.py`.

## Headline

- **First place was called wrong.** Model F gave Flávio Bolsonaro a 36% chance of finishing first; he finished first (47.03% against 45.16%). Every model and baseline favoured Lula for first place.
- **Shares were close; the top-two margin was not.** F's share error was 1.06 percentage points (pp) on average across 6 categories, and 6 of 6 results fell inside its 80% intervals. Its top-two margin missed by 3.6 pp.
- **A runoff was expected and happened.** F gave a first-round win 15% and the Flávio Bolsonaro vs Lula runoff 85%.
- **Who was elected: close to a coin flip, and the coin landed on the slightly favoured side.** The model gave Lula 50.5%; Brier 0.490 against 0.500 for a 50/50 guess.
- **Runoff share inside the interval.** The pre-first-round head-to-head median for Flávio Bolsonaro was 50.3%; the result was 48.21% (error +2.1 pp), inside the 80% interval 45.5% to 55.1%.

![Dot-and-interval chart of the five named candidates' first-round shares: model F's forecast mean with 80% and 94% intervals, and the result as a diamond. Flávio Bolsonaro's result sits near the top of his 80% interval; Lula's sits just below his forecast mean. UNREGISTERED.](../figures/scorecard_2026_unregistered.png)

## First round: models and baselines

| Forecast | Share MAE (pp) | Top-two margin error (pp) | P(actual first place) | First-place Brier | 80% coverage | 94% coverage |
|---|---|---|---|---|---|---|
| F | 1.06 | -3.6 | 0.36 | 0.826 | 100% | 100% |
| E | 2.34 | -5.7 | 0.23 | 1.193 | 83% | 100% |
| E0 | 2.06 | -5.7 | 0.05 | 1.792 | 50% | 50% |
| B (baseline) | 2.22 | -4.4 | 0.38 | 0.769 | 83% | 100% |
| C (baseline) | 2.58 | -6.3 | 0.33 | 0.900 | 83% | 100% |
| D (baseline) | 1.62 | -5.0 | 0.17 | 1.384 | 67% | 100% |

Margin error is predicted minus actual for (actual first minus actual second); a negative value means the forecast had the eventual leader too far behind. Baselines: B latest poll per pollster over 14 days, C final Datafolha poll, D final AtlasIntel poll; their first-place probabilities are the calibrated conversions stored in `outputs/baseline_comparison_2026.csv`. As in the backtest, a baseline's share MAE and margin come from its point value and its coverage from its calibrated conversion in `outputs/forecast_latest.json`.

## What this does and does not show

- One election cannot establish skill or its absence; the backtest covered three.
- The backtest criterion H3 found F's first-place probabilities worse than baseline B's. In 2026 F again scored slightly worse than B on first place (Brier 0.826 vs 0.769) while beating every baseline on share error. F's election-day term, learned from 2014-2022, moves share toward the candidate second in the polls; in 2026 it moved in the right direction but not far enough.
- These are summaries scored five days before the vote, not the eve freeze. The registered comparison with PollingData and market benchmarks is not possible, because those benchmarks were to be captured at the freeze.
- Results are the TSE count at 100% of sections as reported by secondary sources; they must be replaced by the TSE reading (Addendum 06 s.2) before these numbers are final.

Label: UNREGISTERED: scores the PRELIMINARY forecast (cutoff 2026-09-29); the registered freeze did not happen.

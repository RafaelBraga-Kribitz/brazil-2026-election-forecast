# Pre-registration: Brazil 2026 presidential first-round forecast

**Status:** registered before any historical backtest was run and before any 2026 model was fitted.
**Registered:** 2026-09-29 (UTC). The git commit that adds this file and `PREREG.sha256` is the registration
record. This file is never edited after registration. Changes go into dated `PREREG_ADDENDUM_*.md` files that
say what changed, why, and whether the change was made before or after seeing any result.

**Scope of this registration:** the 2026 **first round** (Sunday 2026-10-04) plus the historical retrodiction
(2014, 2018, 2022; both rounds). A 2026 runoff forecast, if a runoff occurs, requires its own addendum and freeze.

---

## 1. Research question

How accurate and how well calibrated is a transparent Bayesian aggregation of publicly registered national polls,
compared with simple poll-based baselines and public benchmarks, when every forecast uses only information
available at its forecast time? The election is the evaluation environment. The project evaluates forecasting
methods, not candidates.

## 2. Data and inclusion rules

- **Poll toplines.** Wikipedia PT "Pesquisas de opinião para a eleição presidencial no Brasil em {year}",
  pinned to an exact revision (oldid), is the primary source. Wikipedia EN is a gap-filler. For 2014 the only
  source with sample sizes is the EN "2014 Brazilian general election" article (revision 1369923991).
  On conflict: original pollster release > Wikipedia PT > Wikipedia EN. Conflicts are recorded, never averaged.
- **Included:** national, stimulated (*estimulada*) presidential vote-intention polls with known sample size and
  field end date, fieldwork ending within the modelling window (below).
- **Excluded:** spontaneous, rejection, state-level and exit polls; polls without sample size or dates; exact
  duplicates.
- **Pollsters:** every pollster meeting the inclusion rules. No pollster is excluded for its track record
  (avoids a forking path). Sensitivity: pollsters with at least 3 polls in the window.
- **Scenario rule (round 1).** For each poll keep one scenario: drop scenarios containing a candidate who is not on
  the registered ballot and polls at or above 3%. Among the rest prefer total-respondent tables, then the scenario
  with most ballot candidates, then the lexicographically first label. Off-ballot names under 3% fall into
  "Others". The ballot is the TSE registration list, public before every horizon used. For 2018, T-30 is
  2018-09-07: the TSE had barred Lula's candidacy on 2018-09-01, so scenarios with Haddad are information-fair.
- **Scenario rule (round 2).** Only polls fielded after the first round, for the actual runoff pair (known once
  the first round is counted).
- **2026 ballot.** The 13 registered candidates listed in Wikipedia PT "Eleição presidencial no Brasil em 2026"
  revision 73078529, recorded in `data/manual/ballot_2026.csv`. A TSE ruling that changes the ballot before the
  freeze is applied and logged in an addendum.

## 3. Transformations

- **Valid-vote conversion (assumption, not fact).** Valid share = candidate share / sum of all candidate shares in
  the same poll scenario. This allocates undecided respondents proportionally and excludes stated blank/null
  intentions, mirroring the TSE's exclusion of blank and null ballots. Undecided is a poll response, never a
  ballot category. Reported shares are preserved. Most historical tables combine undecided with blank/null,
  so the two cannot be allocated differently. Sensitivity: leader-weighted allocation (section 10).
- **Poll dependence.** Within a pollster, fieldwork windows may not overlap. Walking back from each pollster's most
  recent available poll, an earlier poll is dropped if its fieldwork ends on or after the start of the most
  recently kept poll. The rule is applied separately at each forecast cutoff, using only polls available then.
  Sensitivity: rule off.
- **Information time.** A poll is usable at cutoff date *t* only if its fieldwork ended on or before *t* and, where
  a publication date is recorded, it was published on or before *t*. The horizon-*h* cutoff is election day
  minus *h* days. "Eve" (*h* = 1) uses fieldwork ending up to the day before the election.
- **Windows.** Round 1: fieldwork ending in the 60 days before election day. Round 2: fieldwork ending after the
  first round.
- **Candidate grouping.** At each cutoff, a candidate is modelled individually if their mean valid share over polls
  in the preceding 14 days is at least 3%, up to 5 candidates. All others are pooled as "Others". Round 2
  models one series (the first-listed runoff candidate); the other share is the complement.

## 4. Model

Independent valid-share Gaussian random walks, one per series *s*, on a daily grid from the window start to
election day:

- `mu[s,0] ~ Normal(m0[s], 10)` (m0 = mean of the series' polls in the first two weeks of the window);
  `mu[s,t] = mu[s,t-1] + sigma[s,t] * z`, with `z ~ Normal(0,1)` (non-centred).
- **Volatility.** `sigma_rw[s] ~ HalfNormal(0.5)` pp/day. Two-regime variant: innovations entering the final
  10 days are scaled by `rho[s] ~ LogNormal(0, 0.75)` (prior median 1, so no late-movement direction is
  assumed). Single-regime variant: `rho = 1`.
- **Observation model.** `y_i ~ Normal(mu[s, t_i] + house[s, pollster_i], sqrt(sampling_var_i + sigma_ns[s]^2))`,
  where `sampling_var_i = 100^2 p_i(1-p_i)/n_i` (simple random sampling reference, design effect 1) and
  `sigma_ns[s] ~ HalfNormal(2)` is the estimated non-sampling error. *t_i* is the fieldwork midpoint.
- **House effects.** `house[s,:] = sigma_house[s] * ZeroSumNormal`, `sigma_house ~ HalfNormal(2)`: relative
  deviations that sum to zero across the pollsters in each fit. The latent state is the average-pollster
  consensus. Industry-wide deviation is handled only by the election-day term.
- **Inference.** PyMC NUTS, 4 chains × 1000 draws after 1000 tuning steps, target_accept 0.95, seed 20261004.
  **Convergence criteria:** R-hat ≤ 1.01, bulk and tail ESS ≥ 400, divergent transitions ≤ 1% of draws. On failure:
  refit with target_accept 0.99 and 2000 tuning steps. If it still fails, the fit is reported as non-converged
  and flagged in every table that uses it.
- **Simplex projection.** Election-day draws are clipped at 0 and renormalised to 100% (round 1). Round 2 uses the
  complement.

## 5. Election-day deviation term (round-specific, leave-one-election-out)

- **Hypothesis under test, not an assumed fact.** The planning material suggested larger first-round than runoff
  deviations, with the top two candidates underestimated and minor candidates overestimated. No causal mechanism is
  assumed.
- **Deviation.** `d[e,c] = observed valid share − eve forecast mean` (latent model, simplex-projected), for past
  election *e* and category *c*.
- **Roles** come from the forecast ranking, never from candidate identity or the outcome. Round 1: `rank1`,
  `rank2`, `rest` (other named candidates and Others). Round 2: `rank1` (the other share is the complement).
- **Model F (primary).** `d ~ Normal(mu[role], sigma)`, `mu[role] ~ Normal(0, 3^2)`, `sigma ~ HalfNormal(3)`.
- **Model E.** `d ~ Normal(0, sigma)`, `sigma ~ HalfNormal(3)`.
- **Estimation.** Round types are estimated separately. `mu` is integrated analytically given `sigma`, and
  `sigma` is evaluated on a grid.
- **Forecast draws.** latent draw + `mu[role] + sigma·ε` per category, then the simplex projection.
- **Leave-one-election-out (LOEO).**

  | Target | Deviations used |
  | --- | --- |
  | 2014 | 2018 + 2022 |
  | 2018 | 2014 + 2022 |
  | 2022 | 2014 + 2018 |
  | 2026 | 2014 + 2018 + 2022 |

  A backtest forecast never uses its own election's deviations. The same LOEO rule applies to the error
  distributions of the probabilistic baselines. Enforced by `tests/test_loeo.py`.
- **Model E0 (diagnostic).** Latent random walk only, with no election-day error at all.

## 6. Historical backtest

- **Coverage.** 2014, 2018 and 2022, rounds 1 and 2.
- **Horizons.** Round 1 at T-30, T-14, T-7 and eve. Round 2 at T-14, T-7 and eve.
- **Minimum data.** A horizon is fitted only if at least 8 polls are available. Skipped cells are reported as N/A
  with the poll count.
- **Scoring.** Every forecast is scored against the official result.
- **Current result source.** Official results come from a secondary source that cites the TSE, with pinned
  revisions and vote counts summing to the valid total. They are reconciled against TSE files when the manual
  downloads are available (`scripts/check_readiness.py`). Any change is logged.

## 7. Baselines and benchmarks (same information cutoff as the model)

| ID | Baseline | Probabilistic version |
| --- | --- | --- |
| A | PollingData published average, snapshotted at the freeze (2026 only; no historical snapshots) | Point only. Probabilistic metrics N/A. |
| B | Latest poll per pollster with fieldwork ending in the 14 days to cutoff, equal weight per pollster | Calibrated probabilistic conversion |
| C | Final Datafolha poll available at cutoff (within 14 days) | Calibrated probabilistic conversion |
| D | Final AtlasIntel poll available at cutoff (within 14 days) | Calibrated probabilistic conversion; selected on past performance, so read with winner's-curse caution |
| E | Model E above | — |
| F | Model F above (primary) | — |
| Optional | Polymarket price snapshot at the freeze | Scored only for events it prices; never called ground truth |

The **calibrated probabilistic conversion of a point baseline** is: point + Normal(0, RMSE), where RMSE is that
baseline's own LOEO historical error at the same round type and horizon, followed by the same simplex projection.
The baselines themselves did not publish these probabilities.

## 8. Metrics

- **Share MAE** (pp) over all forecast categories, including Others.
- **Top-two margin error** (pp): predicted minus observed margin between the two named candidates with the highest
  observed shares. Reported both signed and absolute.
- **Interval coverage.** 80% and 94% **equal-tailed posterior predictive intervals**. These are not HDIs and not
  confidence intervals.
- **Round-1 events:**
  - first place: multi-category Brier score and log score;
  - "leader exceeds 50% of valid votes": Brier score.
- **Round-2 event:** the winner, with Brier and log score.
- **Probability smoothing.** Monte Carlo event probabilities use (count + 0.5)/(N + 1).
- **Aggregation.** Means across election-rounds per horizon; the eve horizon is the primary backtest summary.
  With six rounds these are descriptive summaries, not significance tests.

## 9. Model-selection rules (fixed now, applied to the historical backtest only)

1. **Volatility regime.** Both variants are fitted and scored.
   - The two-regime variant becomes the production random walk only if two conditions hold:
     - its mean eve-horizon log score on first place, averaged over the six rounds, beats the single-regime
       variant by more than 0.05 nats;
     - its share MAE is not worse by more than 0.1 pp.
   - Otherwise the simpler single-regime variant is used.
   - This choice uses all three historical elections, so it is in-sample. Both variants' backtests are published.
2. **Primary model.** F stays the pre-registered primary model whatever the backtest shows. E, E0 and all baselines
   are always reported beside it.
3. **Priors.** No prior is changed after seeing backtest or 2026 results unless the change is logged in an
   addendum as exploratory.

## 10. Sensitivity analyses (reported, never used to pick the headline)

- **Scope.** Run on the eve-horizon backtest and on the final 2026 forecast.
- **Settings varied, one at a time:**

  | Setting | Values |
  | --- | --- |
  | Non-sampling error prior `sigma_ns` scale | 1, 2, 4 |
  | Election-day `tau` | 1.5, 3, 6 |
  | Election-day `sigma` scale | 1.5, 3, 6 |
  | Volatility regime | single, two |
  | Undecided allocation | proportional; or leader-weighted (the non-candidate pool is allocated in proportion to share², favouring leading candidates) |
  | Poll inclusion | all pollsters, or pollsters with ≥ 3 polls |
  | Dependence rule | on, off |
  | Final-week weighting | all window polls, or only the final 14 days before cutoff |

## 11. Freeze and publication

- **Final freeze.** Saturday 2026-10-03, 22:00 BRT (2026-10-04 01:00 UTC).
  - The poll table is re-parsed from the Wikipedia PT/EN revisions current at the freeze, and their oldids are
    recorded.
  - Polls published after the freeze are excluded.
  - PollingData and Polymarket are snapshotted at the same time, recording timestamp, values, institutes,
    methodology note, market ID, question, price and volume.
- **Freeze package.**
  - `outputs/freeze/forecast.json`, `forecast.csv`, `poll_snapshot.csv`, `baseline_snapshot.csv`,
    `MODEL_VERSION.txt`
  - `forecast_hash.txt` (SHA-256 of each file)
  - the git commit and the source revisions
- **Preliminary forecasts.** Forecasts published before the freeze are labelled **PRELIMINARY**, carry their own
  cutoff, and are never compared with baselines taken at a different cutoff.
- **Post-election scoring.** The scorecard is produced from the frozen files and TSE final totals. It is published
  whatever the result. The frozen forecast is never rewritten. The post-mortem separates pre-election information
  from post-election analysis.

## 12. Success and failure criteria (stated before any result; not designed to be met)

**Scientific success**, independent of forecast accuracy:
- a clean clone reproduces validation, backtest, forecast and figures;
- leakage tests pass;
- the forecast is frozen and hashed before polls open;
- the scorecard is published unchanged.

**Model performance criteria** (each reported as met / not met):
- H1. Historical, eve horizon: F's mean share MAE ≤ Baseline B's.
- H2. Historical, eve horizon: F's pooled 94% coverage across categories is in [85%, 100%], and 80% coverage is in
  [65%, 95%]. The historical categories number fewer than 30, so this criterion is weak.
- H3. Historical, eve horizon: F's mean first-place Brier ≤ Baseline B's calibrated conversion.
- H4. Historical: F's mean share MAE ≤ E's (does the election-day mean term help out of sample?).
- L1. 2026: F's share MAE ≤ Baseline A (PollingData) and ≤ Baseline B.
- L2. 2026: F's absolute top-two margin error ≤ Baseline B's.
- L3. 2026: number of categories whose observed share lies inside F's 94% and 80% intervals.

One election gives weak evidence about long-run skill. The six historical rounds carry more weight, and even they
cannot support strong calibration claims.

## 13. Fallback scope

- If the 2026 poll table cannot be parsed reliably at the freeze, the forecast uses the last reliably parsed
  revision. The gap is disclosed.
- If a historical horizon has fewer than 8 polls, it is N/A.
- If F fails convergence at the freeze, E is published as the primary model with an addendum explaining why.
- The full compositional (ALR/softmax) model is out of scope for this registration.

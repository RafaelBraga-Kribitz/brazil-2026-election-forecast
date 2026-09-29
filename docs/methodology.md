# Methodology

The binding specification is [`PREREG.md`](../PREREG.md) with its dated addenda. This page explains the same design
in prose and maps each step to the code that implements it.

## Pipeline

```
Wikipedia revisions (pinned oldid) --> parse ------------> canonical poll rows      brfc/ingest/wiki_{year}.py
release records (2 final polls)    --> build_release_rows ^                          scripts/build_release_rows.py
                                        |
                                        v
scenario rule -> valid-vote conversion ------------------> valid-vote rows          brfc/transform.py: prepare
                                        |
      per forecast cutoff t:            v
information-time filter -> dependence rule -> grouping --> information set at t    brfc/transform.py: polls_for_forecast
                                        |
                                        v
Bayesian random walks (PyMC NUTS) -----------------------> latent election-day draws brfc/model.py: fit
                                        |
                                        v
LOEO election-day term (roles by forecast rank) ---------> forecast draws (E, F)    brfc/election_day.py
                                        |
                                        v
baselines B/C/D + calibrated conversion; scoring --------> backtest tables          brfc/baselines.py, brfc/scoring.py
```

Stage 1 (fits) never loads results. Stage 2 (evaluation) is the only code that reads them. Tests enforce this split.

## 1. From a poll table to valid votes

Brazilian official results count only **valid votes**: blank and null ballots are excluded. Polls instead report
shares of all respondents, including stated blank/null intention and undecided respondents. The conversion divides
each candidate's share by the sum of candidate shares in that poll scenario. This amounts to allocating undecided
respondents in proportion to decided ones. That is **an assumption**, and the leader-weighted sensitivity tests it.
Reported shares are kept alongside the converted ones.

When a poll offers several scenarios, one is kept deterministically: the scenario closest to the registered ballot
(Addendum 01).

## 2. What each forecast is allowed to see

A forecast for cutoff date *t* uses only polls whose fieldwork ended by *t* (and, where recorded, were published by
*t*). The dependence rule and candidate grouping are then computed **inside** that information set, so a later
poll can never decide which earlier poll is used. A perturbation test changes, adds and overlaps polls after *t* and
checks that the information set is byte-identical.

## 3. The random walk

Each series (a named candidate or "Others") is a daily Gaussian random walk on the valid-share scale. Polls observe
the walk plus a pollster house effect, with noise of two kinds:

- **sampling noise** from the sample size, `p(1-p)/n`;
- **non-sampling noise** `sigma_ns`, estimated from the data. Most Brazilian polls use quota or online designs, so
  the nominal sampling error understates the real spread. `sigma_ns` absorbs the excess and is estimated, not
  fixed.

House effects sum to zero across pollsters, so they measure **relative** deviation from the consensus. Common
industry error is left to the election-day term. The two-regime variant lets volatility change in the final 10
days; a pre-registered rule applied to the historical backtest decides whether it is used.

**Why independent walks, not a compositional model.** Independent walks with renormalisation after sampling are
simple, converge reliably and can be explained in one paragraph. They ignore the negative correlation between
candidates' shares. An ALR/softmax model would capture it, but would cost time the 2026 deadline did not allow.
The trade-off is recorded as a limitation.

## 4. The election-day term

The latent walk estimates where the polling consensus will be on election day. Historically, the official result
differed from that consensus, and differently in first rounds than in runoffs.

- **Measurement.** For each past election, the model's own eve forecast is compared with the result. Each
  deviation is labelled with a **forecast-rank role**: #1, #2 or the rest. Roles never refer to a candidate's
  identity or party.
- **Model F** learns a mean deviation per role plus a spread.
- **Model E** learns only a spread, with mean zero.
- **Leave one election out.** The backtest for election *k* learns these terms only from the other elections, so it
  is genuinely out of sample. The 2026 forecast uses all three.
- **Interpretation.** The term describes *historical deviation*. It does not identify why the deviation happened:
  late decisions, turnout, sampling frames and questionnaire effects cannot be told apart with these data.

## 5. Baselines on equal terms

- **Same information set.** Point baselines use the same cutoff and the same information set as the model.
- **Probabilistic conversion.** To score their calibration, each gets a calibrated probabilistic conversion: point
  plus normal noise with that baseline's own leave-one-election-out historical RMSE. The baselines did not publish
  these probabilities; the label says so everywhere.

## 6. Scoring

The metrics are share MAE, top-two margin error, and 80% and 94% equal-tailed interval coverage. There are also
Brier and log scores for first place, a first-round win, and the runoff winner. Six historical rounds and one live
election are too few for strong claims about calibration. Every table reports the number of scored rounds.

## 7. Who is elected: the conditional forecast

[`PREREG_ADDENDUM_04.md`](../PREREG_ADDENDUM_04.md) section 4 extends the scope from the first round to the
question of who is elected. The forecast is frozen with the first-round package, at the same cutoff. Every output
carries the label "pre-registered; validated on 3 elections only", and every probability is a probability of being
elected under this model.

- **Paths.** Each first-round draw of model F ends in one of two ways.
  - An **outright win**: a named candidate is above 50% of valid votes.
  - A **runoff pair**: otherwise, the two named candidates with the largest shares. "Others" is never a candidate.
- **Pair masses.** P(pair) is the share of first-round draws that end in that pair with no outright win.
- **Head-to-head model.** Each pairing gets its own random walk, fitted from runoff polls fielded before the first
  round: the random walk of section 3 above, on the valid share of the pair's alphabetically first candidate.
  - It uses the production volatility variant and a window starting 60 days before the first round.
  - The information cutoff is the first-round freeze. The walk is projected to runoff day, about 22 to 28 days
    later.
  - Valid-vote conversion, the information-time filter and the dependence rule are applied as registered.
  - A pair is fitted if it has at least 8 head-to-head polls at the cutoff. Pairs are chosen from information
    available at the cutoff, never from the first-round result.
- **Combination.**
  `P(X elected) = P(X outright) + Σ_pairs P(pair and no outright) × P(X wins | pair)`, where P(X wins | pair) is the
  share of head-to-head draws in which X is above 50%.
- **Independence assumption.** First-round draws and head-to-head draws are treated as independent.
  - The two sets of errors may be related, for example when a candidate's first-round and runoff results both differ
    from the polls in the same direction.
  - The combination does not model such a link, and its effect on the probabilities is not estimated.
- **Unmodelled mass.** A pair without a head-to-head fit keeps its probability in a separate "unmodelled" bucket.
  That mass is never reallocated, so the candidate probabilities and the unmodelled bucket sum to 1.
- **Head-to-head election-day term.**
  - The deviation is the official runoff valid share of the candidate ranked first in the pair's forecast, minus that
    forecast's mean on runoff day. It is taken from the eve-cutoff fit of the pair that actually advanced.
  - It is estimated leave one election out, with the registered error model. The 2026 forecast uses all three
    historical elections.
  - **E (zero mean) is primary.** With three elections, a mean term cannot be distinguished from zero. The
    first-round rationale for F, a pattern in which the top two candidates were underestimated and minor candidates
    overestimated, has no counterpart in a two-candidate contest.
  - F and E0 are reported beside E.
- **Reported combinations.** The primary combination is first-round F with head-to-head E. First-round F with
  head-to-head F, and E with E, are reported beside it.
- **Backtest.** 2014, 2018 and 2022 are scored at the first-round eve (primary) and at T-7.
  - Head-to-head share error, margin error and interval coverage are computed for the pair that advanced.
  - "Who was elected" gets a multi-category Brier score (including the unmodelled bucket), a log score and
    P(actual winner).
  - Baselines B and C are run end to end with their calibrated conversions.
  - Criteria HR1 to HR3 are reported as met or not met. Three elections are very weak evidence.
- **Code.** Head-to-head fits and the backtest are in `brfc/runoff.py` and `scripts/run_runoff_backtest.py`
  (`make runoff-backtest`). The combination is in `brfc/conditional.py`. The 2026 forecast is
  `scripts/forecast_2026_president.py` (`make president`), written to `outputs/president_2026.json`.

## 8. The post-first-round runoff forecast

Addendum 04 section 1B registers a second runoff forecast, made once the first round is counted. It is frozen on
Saturday 2026-10-24 at 22:00 BRT (2026-10-25T01:00:00Z), with information cutoff 2026-10-24, the eve horizon.

- **Known information.** At that point the first-round result and the advancing pair are known. The pair is typed
  from the official TSE count with `--pair`, first place first. The script refuses to run without it.
  - The script sets the 2026 pairing for that run only and records it in the output.
  - No 2026 result is read from any file.
- **Registered round-2 design.** This forecast uses the round-2 rules of the original registration, not the
  head-to-head design of section 7.
  - **Polls.** Only round-2 polls of the advancing pair with fieldwork ending after the first round (PREREG s.2).
  - **Model.** One random walk on the first-listed candidate's valid share; the other share is its complement
    (PREREG s.3 and s.4).
  - **Election-day term.** The round-2 term of section 4, with **model F primary**, and E and E0 reported. It is
    trained on the 2014, 2018 and 2022 round-2 eve deviations. F stays primary here because PREREG s.9.2 and
    Addendum 04 s.1B keep it primary for round 2. Addendum 04 made E primary only for the pre-first-round
    head-to-head term.
  - **Baselines.** B and C use their calibrated conversions, with historical round-2 errors at the registered
    horizon nearest to the forecast's horizon (the eve, at the freeze).
- **Outputs.** `scripts/forecast_2026_runoff.py` (`make runoff-2026`) writes `outputs/runoff_2026.json`. At the
  freeze it also writes the package `outputs/freeze_runoff/`: `runoff.json`, `runoff.csv`, `poll_snapshot.csv`,
  `baseline_snapshot.csv`, `MODEL_VERSION.txt` and `forecast_hash.txt`. The steps are in
  [`freeze-procedure.md`](freeze-procedure.md) section B.

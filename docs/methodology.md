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

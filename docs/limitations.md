# Limitations

These limits are stated before any backtest score was computed. The post-mortem will say which ones mattered.

## Evidence base
- **Three historical elections, six rounds.** The election-day term for each backtest target is learned from only
  two elections, and for 2026 from three. Per forecast-rank role that is two or three numbers, so its mean is
  shrunk toward zero by the prior. Interval-coverage estimates rest on fewer than 30 categories per horizon, and
  categories within an election are correlated. No strong calibration claim is possible.
- **One live election.** The 2026 first round yields a handful of candidate shares and one first-place outcome. A
  good or bad score is weak evidence about long-run skill.

## Data
- **Toplines come from Wikipedia, a crowdsourced source.** Mitigations:
  - revisions are pinned (`oldid`);
  - PT/EN conflicts are recorded;
  - every historical final poll is verified against its release (71/71 match within 0.5 pp; two missing final polls
    were added);
  - a seeded 10% random sample is spot-checked.
  Rows outside those checks are unverified.
- **2014 is thin.** The only table with sample sizes (Wikipedia EN) lacks 12 late first-round polls that the PT
  page lists without sample sizes. The 2014 first round therefore rests on 16 polls at the eve horizon, and
  T-30 is below the 8-poll minimum.
- **Blank/null and undecided responses are combined** in most historical and 2026 tables. The valid-vote conversion
  (proportional allocation) cannot be checked against a split. The leader-weighted sensitivity bounds its effect.
- **Official results come from secondary sources citing the TSE,** with vote counts summing exactly to the valid
  totals. Reconciliation against TSE files is pending, because TSE servers refuse scripted downloads; this project
  does not bypass that.
- **Publication dates are mostly unknown.** The information-time rule falls back on fieldwork end dates. A poll
  published after the cutoff but fielded before it could enter a historical forecast one day early. For the 2026
  freeze this cannot happen: the table is parsed from the Wikipedia revision current at the freeze time.

## Model
- **Independent random walks** ignore the negative correlation between candidates' shares. Renormalisation after
  sampling restores the simplex, but not the correlation structure. A compositional (ALR/softmax) model was out of
  scope for the deadline.
- **House effects are relative** (they sum to zero across pollsters). They cannot reveal an error shared by the
  whole industry. Only the election-day term, learned from past elections, addresses that.
- **The dependence rule is simple.** Overlapping waves from the same pollster are dropped. There is no
  pollster-level covariance and no effective-sample-size model.
- **Design effect = 1.** Sampling variance uses the simple-random-sampling formula. The estimated non-sampling term
  absorbs the rest. For large online samples the sampling term is negligible, and the non-sampling term dominates
  by design.
- **Roles, not mechanisms.** The election-day term is defined by forecast rank (#1, #2, rest). It describes where
  past eve forecasts deviated. It does not explain why: late decisions, turnout differences, sampling frames and
  question wording are not separable with these data.
- **The volatility-regime choice is in-sample.** The pre-registered rule compares both variants on all three
  historical elections, and both backtests are published.

## Benchmarks
- **PollingData** (baseline A) exists only for 2026, as a single snapshot. No historical snapshots were collected,
  so there is no probabilistic version and no backtest.
- **Polymarket** prices are one market's opinion under thin liquidity and access restrictions. They are recorded as
  a benchmark only and are not treated as ground truth.

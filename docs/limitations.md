# Limitations

These limits were first committed before the historical backtest results were committed. Only local commit times,
which the author's machine sets, record that order; the first GitHub push contained both (see "Pre-registration
record" below). Updates made on 2026-09-29, after the backtests had been scored: the TSE reconciliation entry reports
the finished check, the 2014 entry reports the labelled post-result revision and no longer quotes an untraced poll
count, the evidence-base entry separates the "rest" role, and the entry "Longer horizons" and the sections
"Conditional forecast (who is elected)" and "Pre-registration record" were added. The post-mortem will say which
limits mattered.

## Evidence base
- **Three historical elections, six rounds.** The election-day term for each backtest target is learned from only
  two elections, and for 2026 from three. For the #1 and #2 roles, and for the runoff term, that is two or three
  numbers, so their means are shrunk toward zero by the prior. The "rest" role pools every other category: its mean
  is learned from 7, 5 and 6 first-round deviations for the 2014, 2018 and 2022 targets, and from 9 for 2026
  (`outputs/election_day_deviations.csv`). Interval-coverage estimates rest on fewer than 30 categories per horizon,
  and categories within an election are correlated. No strong calibration claim is possible.
- **Longer horizons.** Criteria H1-H4 are scored at the eve only. At earlier first-round cutoffs model F's intervals
  were too narrow: its 94% intervals covered 12 of 15 categories at T-7, 11 of 15 at T-14 and 8 of 11 at T-30
  (`outputs/calibration.csv`). Its first-place Brier was worse than B's and E's at every first-round horizon (T-7:
  F 0.190, B 0.073, E 0.046; T-14: 0.173, 0.062, 0.050). At T-7 its share MAE (4.79 pp) was above B's (4.54) and
  E's (4.48) (`outputs/backtest_summary.csv`). The PRELIMINARY 2026 forecast has a 5-day horizon, which the backtest
  did not score.
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
- **2014 is thin.** The only table with sample sizes (Wikipedia EN) lacks late first-round polls that the PT page
  lists without sample sizes. The registered 2014 first round therefore rests on 17 polls at the eve horizon
  (including the final Ibope poll added from its release record, Addendum 02), and T-30 has 6 polls, below the
  8-poll minimum. Research of original releases later found sample sizes for 11 first-round polls missing from the
  EN table (`data/interim/polls_2014_supplement.csv`). Because that was decided after the first-round scores had
  been seen, those rows are a labelled post-result revision (Addendum 04 section 3). They load only on request and never feed
  the headline, the election-day term used for 2026, or any selection rule. With them the 2014 eve information set
  has 27 polls; the mean first-round eve share error moves from 2.33 to 2.29 pp for model F and from 3.19 to
  2.85 pp for baseline B (`outputs/revision_2014_first_round.csv`). The registered values stay the reference.
- **Blank/null and undecided responses are combined** in most historical and 2026 tables. The valid-vote conversion
  (proportional allocation) cannot be checked against a split. The leader-weighted sensitivity bounds its effect.
- **Official results: secondary sources, reconciled with the TSE files.** The scored results
  (`data/manual/results_secondary.csv`) come from secondary sources citing the TSE, with vote counts summing
  exactly to the valid totals. They were reconciled against the TSE "Votação nominal por município e zona" files for
  2014, 2018 and 2022: all 41 candidate vote counts across the six rounds are equal, with 0 differences
  (`outputs/tse_reconciliation_{2014,2018,2022}.csv`; file hashes in `outputs/readiness.json`). TSE servers refuse
  scripted downloads, so the files were downloaded manually in a browser and are not redistributed. A clean clone
  can repeat the reconciliation only after the same manual download (`data/raw/README.md`).
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
- **Additive role shifts can push small categories toward 0.** Model F adds the role mean to every category on
  the valid-share scale and then clips at 0 and renormalises. In the 2026 PRELIMINARY forecast (cutoff
  2026-09-29) the "rest" mean is -1.31 pp (`outputs/forecast_latest.json`), applied to each of Augusto Cury, Renan
  Santos, Ronaldo Caiado and "Others". "Others" has a latent polling consensus of 1.76% (model E0 mean), but its
  model-F median is 0.39%, against 1.78% under model E, and the lower bound of its 80% interval is 0.00%. The three
  named "rest" candidates also have 80% lower bounds of 0.00% under F. This is a property of the additive
  specification, not evidence about those candidates. A multiplicative or compositional term would not do this.
- **The volatility-regime choice is in-sample.** The pre-registered rule compares both variants on all three
  historical elections, and both backtests are published.

## Conditional forecast (who is elected)
Added 2026-09-29, after the head-to-head backtest had been scored and the 2026 PRELIMINARY conditional forecast
had been produced. Rules: [`PREREG_ADDENDUM_04.md`](../PREREG_ADDENDUM_04.md) section 4; scores:
[`runoff-backtest.md`](runoff-backtest.md).

- **Independence assumption.** First-round draws and head-to-head draws are combined as if independent. A shared
  error, for example a candidate whose first-round and runoff results both differ from the polls in the same
  direction, is not modelled. Its effect on the probabilities is not estimated, and the backtest has too few
  elections to detect it.
- **Three elections for the head-to-head term.** The head-to-head election-day term rests on three deviations
  (-2.83, +3.50 and -3.52 pp, `outputs/h2h_deviations.csv`); each backtest target learns it from two. The 2026
  term's spread (mean sigma 3.55 pp, `outputs/president_2026.json`) is estimated from those three numbers. Criteria
  HR1-HR3 were met on these three elections, which is very weak evidence. The choice of E as the primary
  head-to-head term was fixed before scoring; F scored better at the T-7 cutoff and worse at the eve cutoff.
- **Pairs without head-to-head polls are unmodelled.** A pair needs at least 8 head-to-head polls at the cutoff.
  The probability of other pairs is reported as "unmodelled" and never reallocated. In the 2026 PRELIMINARY
  forecast all 5 fitted pairs include Lula, and the unmodelled mass is 0.0% only because every first-round draw of
  model F places Lula and Flávio Bolsonaro in the top two (`outputs/forecast_latest.json`). That zero is a property
  of the model, not a certainty.
- **The pair probability depends on the first-round model.** In the 2014 backtest at the T-7 cutoff, the
  first-round model gave the pair that actually advanced a probability of 0.004. The conditional forecast still
  gave the elected candidate 0.686, because the other pair with that candidate (Dilma Rousseff–Marina Silva) was
  also fitted (`outputs/president_backtest.csv`). In 2018 at T-7 the elected candidate received 0.352.
- **Head-to-head polls are fielded before the first round.** The walk is projected from the first-round cutoff to
  runoff day, so the campaign between the rounds is not observed; the post-first-round runoff forecast (frozen
  2026-10-25T01:00:00Z) uses runoff polls fielded after the first round instead.

## Pre-registration record
Added 2026-09-29, after both backtests had been scored.

- **What the repository can show.** In the git history the registration commit (`PREREG.md`) comes first, the first
  version of this file comes next, and the historical backtest results come after both
  (`git log --reverse --format="%h %cI %s"`). Those are local commit times, which the author's machine sets.
- **The first external timestamp.** The first timestamp outside the author's control is GitHub's record of the
  first push, and that push already contained the historical backtest results; the repository was private at the
  time. So the repository alone cannot show that the design was fixed before the backtest ran; that rests on the
  local commit order.
- **Addenda 04 and 05** reached GitHub before the runoff backtest results. This shows the addenda existed before
  those results were pushed, not that no head-to-head fit had been run locally.
- **One day.** The registration, all five addenda, both backtests and the PRELIMINARY forecasts date from the same
  day, 2026-09-29.
- **The 2026 forecast.** The FINAL freeze tag is to be pushed before polls open on Sunday 2026-10-04 (08:00 BRT,
  11:00Z; [`freeze-procedure.md`](freeze-procedure.md)). That will give the 2026 forecast an external timestamp that
  precedes the result.

## Benchmarks
- **PollingData** (baseline A) exists only for 2026, as a single snapshot. No historical snapshots were collected,
  so there is no probabilistic version and no backtest.
- **Polymarket** prices are one market's opinion under thin liquidity and access restrictions. They are recorded as
  a benchmark only and are not treated as ground truth.

# PREREG addendum 05: implementation clarifications for Addendum 04

**Date:** 2026-09-29 (UTC). Written **before any head-to-head fit and before any runoff or conditional-forecast
score**. No design rule changes; this file records choices that Addendum 04 left implicit and corrects its
illustrative pair table.

1. **Pairs fitted.** Addendum 04 §4 fits every pair of ballot candidates with at least 8 head-to-head polls at the
   cutoff. The §2 table listed only the largest pairs. Counts under the rule at the first-round eve cutoff, after
   the dependence rule:

   | Election | Pairs fitted | Pairs |
   | --- | --- | --- |
   | 2014 | 3 | Dilma–Aécio 28, Dilma–Marina 28, Aécio–Marina 13 |
   | 2018 | 10 | Bolsonaro–Haddad 43, Alckmin–Bolsonaro 43, Ciro–Bolsonaro 43, Bolsonaro–Marina 32, Haddad–Alckmin 28, Ciro–Alckmin 23, Ciro–Haddad 15, Alckmin–Marina 10, Ciro–Marina 9, Haddad–Marina 9 |
   | 2022 | 5 | Bolsonaro–Lula 64, Ciro–Bolsonaro 22, Ciro–Lula 21, Bolsonaro–Tebet 15, Lula–Tebet 15 |
   | 2026 (data to 2026-09-28) | 5, each with Lula | Flávio Bolsonaro 59, Caiado 51, Zema 50, Renan Santos 49, Cury 35 |

   The rule, not this table, decides; the table is descriptive.

2. **Scenario tie-break inside a poll.** If a poll reports the same pair twice, the total-respondent table is
   preferred, then the lexicographically first label.

3. **Probability smoothing.**
   - Head-to-head winner Brier and log scores use the registered Monte Carlo rule `(count + 0.5) / (N + 1)`
     (PREREG §8).
   - "Who was elected" probabilities are exact Monte Carlo path frequencies. Their log score uses the floor 1e-4
     (Addendum 04 §5).

4. **Seeds.**
   - Backtest: `crc32(election|h2h|horizon|variant)`, with application seed 1.
   - 2026: seed 20261004 (+1 for model F), the same scheme as the first-round forecast.

5. **Separate cache.** Head-to-head fits live in `data/cache/fits_h2h/`. The registered first-round and
   post-first-round fits cannot be touched by head-to-head repair runs, and vice versa.

6. **Fresh and cached fits are bit-identical.** Draws are stored as float32. Every fit function returns the stored
   draws, not the in-memory float64 draws, so a re-run from cache reproduces a fresh run exactly. This affects only
   fits made from now on. Registered backtest scores were computed from cached draws and are unchanged.

7. **The post-first-round runoff forecast** (Addendum 04 §1B) takes the advancing pair on the command line
   (`scripts/forecast_2026_runoff.py --pair "<A>" "<B>"`), entered from the TSE first-round result. The pair is
   information available at that forecast's cutoff; no 2026 runoff result is ever read.

8. **Evaluation code never passes a target election's outcomes to a forecast function.** Call sites pass only the
   other elections' results.

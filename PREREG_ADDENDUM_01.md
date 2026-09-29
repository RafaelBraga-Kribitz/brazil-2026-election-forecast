# PREREG addendum 01: scenario tie-break and the 2026 ballot

**Date:** 2026-09-29 (UTC), after registration (commit 27789a2), **before any backtest or 2026 model fit**.
No result had been seen when this change was made.

## Change
Section 2 "Scenario rule (round 1)", tie-break order. Registered: prefer total-respondent tables, then most ballot
candidates, then label. **Amended:** prefer total-respondent tables, then the scenario with the **fewest off-ballot
names** (of any size), then most ballot candidates, then label.

## Why
While parsing the 2026 tables, ten recent polls turned out to report two scenarios: "full", which includes Pablo
Marçal, and "without Pablo Marçal". Marçal's candidacy was revoked, and his party (PRTB) registered Leonardo
Avalanche instead (Wikipedia PT "Eleição presidencial no Brasil em 2026", oldid 73078529). Under the registered
tie-break, the "full" scenario would win on label order. It would then keep a non-ballot name, whose share would
fall into "Others", even though the same poll also offers the ballot-matching scenario. The amended rule picks the
scenario closest to the actual ballot. It is a data-handling correction made without reference to any forecast or
outcome.

## Ballot record
The 13 registered 2026 candidates, plus the revoked candidacy, are in `data/manual/ballot_2026.csv`.

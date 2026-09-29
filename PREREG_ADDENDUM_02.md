# PREREG addendum 02: final polls missing from Wikipedia are added from release records

**Date:** 2026-09-29 (UTC). Written after registration and before any backtest score or 2026 fit existed.
Stage-1 random-walk fits were running when this was written; they carry no outcome information. No score,
error or forecast had been computed.

## Change
Section 2 "Poll toplines". The registered precedence is original pollster release > Wikipedia PT > Wikipedia EN.
Verifying every historical final-poll row (`scripts/verify_final_polls.py`) found:

- **71 of 71 checkable values match the release within 0.5 pp.** These are the final-poll values present on the pinned
  Wikipedia revisions.
- **Two final pre-election polls are absent** from the pinned tables:
  - Ibope 2014 round 1 (fieldwork ending 2014-10-04; total-respondent shares published);
  - Quaest 2022 round 1 (fieldwork ending 2022-10-01, BR-02444/2022; only valid-vote shares published).

Under the registered precedence these two polls are added from their release records. The build script is
`scripts/build_release_rows.py` and the output is `data/interim/polls_releases.csv`. Each row has source type
`media_report`, the release URL and the SHA-256 of the verification file.

- The Quaest rows are on a valid-vote basis. They pass through the same conversion, which only renormalises
  rounding.
- Minor candidates at 0% are omitted; they carry no information about valid shares.

## Effect
Only the eve-horizon fits for 2014 round 1 and 2022 round 1 change, and they are refit. No other rule changes.

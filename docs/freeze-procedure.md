# Freeze procedure

The binding rules are [`PREREG.md`](../PREREG.md) section 11 and its addenda. This runbook lists the commands, in
order, for the first-round freeze and, if a runoff is held, the runoff freeze. Commands run in PowerShell from the
repository root. `uv run python` can replace `.venv\Scripts\python.exe` throughout.

| Freeze | Freeze time | Poll cutoff date | Tag pushed before |
| --- | --- | --- | --- |
| First round | Sat 2026-10-03 22:00 BRT = **2026-10-04T01:00:00Z** | 2026-10-03 | polls open, Sun 2026-10-04 08:00 BRT (11:00Z) |
| Runoff | Sat 2026-10-24 22:00 BRT = **2026-10-25T01:00:00Z** | 2026-10-24 | polls open, Sun 2026-10-25 08:00 BRT (11:00Z) |

BRT is UTC-3; Brazil has no daylight saving time.

## Rules for both freezes

- **No code changes.** Code is committed and pushed in the pre-flight step. The freeze commit changes only data and
  outputs, so `MODEL_VERSION.txt` identifies the code that produced the package.
- **Timing.**
  - Poll tables are pinned with `--at` to the Wikipedia revisions current at the freeze time, so the refresh may run
    after the freeze time without admitting later polls.
  - Benchmarks are live pages, so they are captured at the freeze time. The Polymarket fetch runs once; later steps
    re-parse the saved responses (`--no-fetch`) rather than fetching newer prices.
- **Frozen files are never rewritten.** If something is wrong after the tag is pushed, correct it in a new commit
  with a dated addendum. Never move or delete a pushed tag.
- **Wording.** Published text says "probability of being elected under this model". Polymarket values are market
  prices and PollingData values are a published average; neither is ground truth.
- **Nothing is imputed.** Only values that PollingData displayed as numbers are typed in. No probability is computed
  for PollingData.

## A. First-round freeze

### A0. Pre-flight (by Saturday 2026-10-03, daytime)

```powershell
git switch main; git pull --ff-only
git status --short                                   # must be empty
.venv\Scripts\ruff.exe check src scripts tests
.venv\Scripts\python.exe -m pytest -q -m "not slow"
.venv\Scripts\python.exe scripts\check_readiness.py  # exit code 0
Test-Path outputs\regime_selection.json              # True: production random-walk variant fixed
Test-Path outputs\h2h_deviations.csv                 # True: head-to-head deviations, needed in A6
Test-Path outputs\freeze                             # False: no freeze package yet
.venv\Scripts\python.exe scripts\forecast_2026_president.py --help   # lists --freeze-dir, used in A6
```

- **Head-to-head deviations.** `outputs\h2h_deviations.csv` is written by
  `scripts\run_runoff_backtest.py --stage evaluate` (`make runoff-backtest` runs the fits, then the evaluation). If
  the file is missing, run it now and commit the outputs before continuing: A6 stops without it.
- **Ballot.** Confirm that `data/manual/ballot_2026.csv` still matches the TSE registration list. A change requires
  an addendum before the freeze (PREREG s.2).
- **Run time.** Note how long the PRELIMINARY fits took. A5 and A6 must finish, and A8 must be pushed, before
  08:00 BRT.
- **Browser tabs.** Open these tabs ahead of time:
  - the PollingData 2026 presidential page, plus its probability page if it displays one;
  - `https://polymarket.com/event/brazil-presidential-election-first-round-winner`;
  - `https://polymarket.com/event/will-any-presidential-candidate-win-outright-in-the-first-round-of-the-brazil-election`;
  - `https://polymarket.com/event/brazil-presidential-election`.

### A1. 22:00 BRT (01:00Z): Polymarket snapshot

```powershell
.venv\Scripts\python.exe scripts\snapshot_benchmarks.py --label freeze
```

- **Events fetched.** The script fetches three events through the public Gamma API:
  - 943054, first place in round 1;
  - 45924, outright win in round 1;
  - 45915, election winner.
- **Files written.**
  - Raw responses and their provenance records (URL, UTC time, SHA-256) go to `data\raw\benchmarks\`, which is
    git-ignored.
  - The table goes to `data\manual\benchmarks_2026_freeze.csv`. It keeps every market row, including names that
    are not on the ballot; those have an empty `candidate`.
- **Expected warning.** A warning that the PollingData file is missing is normal at this point.
- **Event id warning.** A line `WARNING: <kind>: event id ...` means a slug now points to a different event. Check
  that event on polymarket.com before continuing.
- **Archive.** Save each event page with `https://web.archive.org/save/` and keep the three archive URLs.

### A2. 22:00 BRT (01:00Z): PollingData reading, by hand in a browser

1. **Capture the page.** Read the page at the freeze time. Save a full-page screenshot as
   `data\raw\benchmarks\pollingdata_freeze.png`, which is git-ignored and not redistributed. Save the page with the
   Wayback Machine and keep the archive URL.
2. **Hash the screenshot.** Run
   `(Get-FileHash data\raw\benchmarks\pollingdata_freeze.png -Algorithm SHA256).Hash.ToLower()`.
3. **Write the file.** Create `data\manual\pollingdata_freeze.json`. The field reference is in the docstring of
   `scripts/snapshot_benchmarks.py`.

   ```json
   {"retrieved_utc": "2026-10-04T01:00:00Z", "url": "<page URL>", "archive_url": "<archive URL>",
    "basis": "total_incl_nao_valido", "values_pct": {"<name as displayed>": 0.0},
    "nao_valido_pct": 0.0, "last_poll_date": "2026-10-03",
    "institutes_note": "<institutes listed>", "methodology_note": "<method text as displayed>",
    "screenshot_sha256": "<hash from step 2>",
    "forecast": {"url": "<page URL>", "retrieved_utc": "2026-10-04T01:00:00Z", "archive_url": "",
                 "win_probability_pct": {}, "first_place_pct": {"<name as displayed>": 0.0},
                 "runoff_pct": 0.0, "first_round_outright_pct": null, "method_note": "<as displayed>"}}
   ```

   - **Times and values.** `retrieved_utc` is the time the page was read. Every value is typed exactly as displayed.
   - **Basis.** Use `basis: "valid"` only if the page itself shows valid-vote shares. Otherwise
     `nao_valido_pct` must hold the displayed "não válido" figure.
   - **Omissions.** Leave out a candidate the page does not show, or shows only as "<1%" or similar, and say so in
     `methodology_note`.
   - **Probabilities.** Include `forecast` only if PollingData displays probabilities, and only with the values it
     displays. Set a field to `{}` or `null` when the page does not show it.
     - On 2026-09-30 the probabilities were in the frame
       `https://www.pollingdata.com.br/capa2026/previsao_2026_grafs.html`, which the main page embeds.
     - That frame showed "Chance do Lula ficar em 1º lugar (no 1º turno)", which goes in `first_place_pct`.
     - It also showed "Chance do 2º turno ocorrer", which goes in `runoff_pct`. It showed no election-winner
       probability, so `win_probability_pct` stays `{}`.
     - Never convert one displayed value into another field, for example a runoff chance into
       `first_round_outright_pct`.
   - **Shares on 2026-09-30.** The main estimate showed only Lula, Flávio Bolsonaro, Augusto Cury and "Não Válido",
     on the total basis. Type exactly what is displayed at the freeze.

### A3. Rebuild the benchmark table from the saved responses

```powershell
.venv\Scripts\python.exe scripts\snapshot_benchmarks.py --label freeze --no-fetch `
  --archive-url-first-place <url> --archive-url-first-round-outright-win <url> --archive-url-election-winner <url>
```

- **No new fetch.** Each saved response is checked against its recorded SHA-256. Prices and timestamps stay the
  ones from A1.
- **Check the output.**
  - The printed PollingData valid shares match the page.
  - Every `note: PollingData names not matched` entry is expected, for example "Outros".
  - `data\manual\benchmarks_2026_freeze.csv` exists; A5 copies it into the package.

### A4. Refresh the poll table at the freeze timestamp (after 01:00Z)

```powershell
.venv\Scripts\python.exe scripts\refresh_2026_polls.py --at 2026-10-04T01:00:00Z
Get-Content data\interim\polls_wiki_2026.revision.json
git diff --stat data
.venv\Scripts\python.exe -m pytest -q -m "not slow" tests\test_data_provenance.py
```

- **Revision times.** Both recorded revision timestamps (PT and EN) must be at or before 2026-10-04T01:00:00Z.
- **Conflicts.** Review new rows in `data\interim\conflicts_wiki_2026*.csv`.
- **Fallback.** If the revision at the freeze cannot be parsed reliably, PREREG s.13 applies: use the last reliably
  parsed revision and disclose the gap.

### A5. FINAL first-round forecast

```powershell
.venv\Scripts\python.exe scripts\forecast_2026.py --status FINAL --cutoff 2026-10-03
```

- **Order.** Run this after A3: it adds `data\manual\benchmarks_2026_freeze.csv` to
  `outputs\freeze\baseline_snapshot.csv`.
- **Files written.** `outputs\freeze\` receives `forecast.json`, `forecast.csv`, `poll_snapshot.csv`, `draws.npz`
  (every model's draws, Addendum 06 s.1), `baseline_snapshot.csv`, `MODEL_VERSION.txt` and `forecast_hash.txt`.
- **Check `forecast.json`.**
  - `status` is `FINAL` and `information_cutoff_date` is `2026-10-03`.
  - `poll_source` holds the oldids from A4.
  - `fit_diagnostics` meet PREREG s.4 as amended by Addendum 03.
- **Non-convergence.** If F fails convergence, PREREG s.13 applies: E is published as the primary model, with an
  addendum.

### A6. Conditional president forecast, written into the package

```powershell
.venv\Scripts\python.exe scripts\forecast_2026_president.py --status FINAL --cutoff 2026-10-03 --freeze-dir outputs\freeze
Get-ChildItem outputs\freeze
```

- **Inputs.** It reads the cached first-round fit written in A5 (same status, cutoff and poll revision) and
  `outputs\h2h_deviations.csv` from A0.
- **Files written.**
  - `outputs\president_2026.json`, and a new row block in `outputs\president_history.csv`.
  - `outputs\freeze\president.json` and `outputs\freeze\president.csv`, after which `forecast_hash.txt` is rewritten
    over every file in `outputs\freeze\`. The script stops, and writes nothing into the package, if a file already
    listed in `forecast_hash.txt` is missing or no longer matches its hash.
- **Check the listing.** `president.json` and `president.csv` sit next to `forecast.json`.
- **Headline wording.** The headline is the probability of being elected under this model. Mass on runoff pairings
  without a head-to-head fit is reported as unmodelled.

### A7. Hash and verify the freeze package

Run this after A6, in every case. Recompute `forecast_hash.txt` over every file in the package, using the same
format as `forecast_2026.py`, then verify it:

```powershell
.venv\Scripts\python.exe -c "from pathlib import Path; from brfc.provenance import sha256_file; fz = Path('outputs/freeze'); fs = sorted(p for p in fz.rglob('*') if p.is_file() and p.name != 'forecast_hash.txt'); (fz / 'forecast_hash.txt').write_text(''.join(f'{sha256_file(p)}  {p.relative_to(fz).as_posix()}\n' for p in fs), encoding='utf-8', newline='\n')"
.venv\Scripts\python.exe -c "from pathlib import Path; from brfc.provenance import sha256_file; fz = Path('outputs/freeze'); rows = [x.split('  ', 1) for x in (fz / 'forecast_hash.txt').read_text(encoding='utf-8').splitlines() if x]; files = {p.relative_to(fz).as_posix() for p in fz.rglob('*') if p.is_file()} - {'forecast_hash.txt'}; bad = [n for h, n in rows if sha256_file(fz / n) != h]; print(len(rows), 'hashed;', 'unhashed:', sorted(files - {n for _, n in rows}), '; mismatched:', bad)"
Get-Content outputs\freeze\forecast_hash.txt
```

The verification must print `unhashed: [] ; mismatched: []`. The same check, as the scorecard runs it:
`.venv\Scripts\python.exe -c "from brfc.freeze import verify_package; print(verify_package('outputs/freeze'))"`
must print `[]`.

### A8. Commit, tag, push

Run the lines one at a time and read each output before the next. Each `throw` stops the block before a tag can be
created or pushed on the wrong commit.

```powershell
git add outputs data\interim data\SOURCES.lock.json data\manual
git status --short              # nothing under src\, scripts\ or tests\; nothing from data\raw\
git commit -m "FINAL 2026 first-round forecast: freeze 2026-10-04T01:00:00Z"
if ($LASTEXITCODE) { throw "commit failed: do not tag" }
git ls-tree -r --name-only HEAD outputs/freeze        # lists every package file, including draws.npz
git tag -a freeze-2026-r1 -m "First-round freeze 2026-10-04T01:00:00Z (outputs/freeze/forecast_hash.txt)"
if ((git rev-parse 'freeze-2026-r1^{commit}') -ne (git rev-parse HEAD)) { throw "tag is not on the freeze commit" }
git push origin main
git push origin freeze-2026-r1
```

- **Staging.** `data\manual` is staged as a whole, so a benchmark file that could not be captured (see the failure
  table) does not stop `git add`.

- **Check the push.** The tag appears on GitHub and CI passes on the commit.
- **After the push.** Figures (`make figures`) and any report are built from the frozen files. They are not part of
  the hashed package.
- **Scoring.** The scorecard is later produced from `outputs\freeze\` and the TSE final totals, whatever the result
  (PREREG s.11). See section C.

## B. Runoff freeze (only if a runoff is held)

### B0. Prerequisites, committed and pushed before 2026-10-24 22:00 BRT

- **Registration.** [`PREREG_ADDENDUM_04.md`](../PREREG_ADDENDUM_04.md) section 1B registers the runoff forecast:
  - freeze 2026-10-25T01:00:00Z, poll cutoff 2026-10-24;
  - the registered round-2 design: PREREG s.2 round-2 rule, s.4 model, s.5 round-2 term with model F primary, and
    s.6 horizons;
  - the package directory used by this runbook, `outputs\freeze_runoff\` (a procedural choice; the addenda do not
    fix a directory).

  A further addendum is needed only for a departure from this design.
- **The advancing pair.** Take the two candidates from the official TSE first-round count, first place first.
  - Type them as spelled in `data\manual\ballot_2026.csv`; accents and case are not significant.
  - `scripts\forecast_2026_runoff.py` refuses to run without `--pair`. It sets the 2026 pairing for that run only
    and records it in `runoff.json`.
  - It never reads a 2026 result file.
- **Runoff polls.** Runoff tables are parsed by the same refresh into `data\interim\polls_wiki_2026_runoff.csv`.
  Only polls of the advancing pair with fieldwork ending after the first round enter the forecast. Check that such
  polls appear in this file before the freeze.
- **Keep the first-round package.** `outputs\freeze\` holds the frozen first-round package.
  - Do **not** run `forecast_2026.py --status FINAL` again.
  - Do **not** run `forecast_2026_president.py --freeze-dir outputs\freeze` again.
  - `forecast_2026_runoff.py` refuses to write into a directory that holds `forecast.json`.
- **Pre-flight.**

  ```powershell
  git switch main; git pull --ff-only
  git status --short                                   # must be empty
  .venv\Scripts\ruff.exe check src scripts tests
  .venv\Scripts\python.exe -m pytest -q -m "not slow"
  .venv\Scripts\python.exe scripts\check_readiness.py  # exit code 0
  Test-Path outputs\freeze\forecast_hash.txt           # True: first-round package in place
  Test-Path outputs\freeze_runoff                      # False: no runoff package yet
  .venv\Scripts\python.exe scripts\forecast_2026_runoff.py --help   # lists --pair and --freeze-dir, used in B4
  .venv\Scripts\python.exe scripts\refresh_2026_polls.py            # latest revision: post-first-round runoff polls
  .venv\Scripts\python.exe scripts\forecast_2026_runoff.py --status PRELIMINARY --cutoff <yesterday> --pair "<A>" "<B>"
  ```

- **Refresh first.** The tables committed at A8 end at the first-round freeze, so they hold no runoff poll fielded
  after the first round. Without the refresh the PRELIMINARY run stops with "skipped: 0 polls < 8". B3 later pins
  the tables to the freeze time. Commit the refreshed tables with the PRELIMINARY output, or restore them with
  `git checkout -- data\interim` before B3.

- **PRELIMINARY run.** It times the fit and checks that the historical round-2 fits are cached in
  `data\cache\fits\`. Without them it stops with "missing historical round-2 eve fits"; `make backtest` writes them.
  Its output is labelled PRELIMINARY and carries its own cutoff.

### B1. 22:00 BRT (2026-10-25T01:00:00Z): Polymarket, election winner only

```powershell
.venv\Scripts\python.exe scripts\snapshot_benchmarks.py --label runoff_freeze --events election_winner
```

- **Archive.** Save the event page with the Wayback Machine.
- **First-round events.** They have resolved and are not captured.

### B2. 22:00 BRT (01:00Z): PollingData, then rebuild

- **Read the page.** Follow A2, writing `data\manual\pollingdata_runoff_freeze.json` and saving the screenshot as
  `data\raw\benchmarks\pollingdata_runoff_freeze.png`.
- **Runoff values.** `values_pct` holds the two runoff candidates as displayed.

```powershell
.venv\Scripts\python.exe scripts\snapshot_benchmarks.py --label runoff_freeze --events election_winner --no-fetch `
  --archive-url-election-winner <url>
Test-Path data\manual\benchmarks_2026_runoff_freeze.csv   # True: B4 copies it into the package
```

### B3. Refresh the poll tables at the freeze timestamp (after 01:00Z)

```powershell
.venv\Scripts\python.exe scripts\refresh_2026_polls.py --at 2026-10-25T01:00:00Z
Get-Content data\interim\polls_wiki_2026.revision.json
git diff --stat data
.venv\Scripts\python.exe -m pytest -q -m "not slow" tests\test_data_provenance.py
```

- **Revision times.** Both recorded revision timestamps must be at or before 2026-10-25T01:00:00Z.
- **Conflicts.** Review new rows in `data\interim\conflicts_wiki_2026_runoff.csv`.

### B4. FINAL runoff forecast, written into its own package

```powershell
.venv\Scripts\python.exe scripts\forecast_2026_runoff.py --status FINAL --cutoff 2026-10-24 --pair "<A>" "<B>" --freeze-dir outputs\freeze_runoff
Get-ChildItem outputs\freeze_runoff
```

- **Pair.** `<A>` and `<B>` are first and second place in the official TSE first-round count.
- **Make equivalent.**
  `make runoff-2026 STATUS=FINAL CUTOFF=2026-10-24 PAIR_A="<A>" PAIR_B="<B>" FREEZE_DIR=outputs/freeze_runoff`.
- **Order.** Run this after B2 and B3. It copies the benchmark snapshot taken with `--label runoff_freeze`
  (`data\manual\benchmarks_2026_runoff_freeze.csv`) into `baseline_snapshot.csv`.
- **Files written.**
  - `outputs\runoff_2026.json`.
  - In `outputs\freeze_runoff\`: `runoff.json`, `runoff.csv`, `poll_snapshot.csv`, `draws.npz`,
    `baseline_snapshot.csv` and `MODEL_VERSION.txt`, then `forecast_hash.txt`, rewritten over every file in the
    directory.
- **Check `runoff.json`.**
  - `status` is `FINAL`, `information_cutoff_date` is `2026-10-24` and `registered_freeze_utc` is
    `2026-10-25T01:00:00Z`.
  - `runoff_pair` is the TSE pair, in the order typed.
  - `poll_source` holds the oldids from B3.
  - `election_day_term_trained_on` is 2014, 2018 and 2022, and `label` is
    "pre-registered; validated on 3 elections only".
  - `fit_diagnostics` meet PREREG s.4 as amended by Addendum 03.
- **Non-convergence.** If F fails convergence, PREREG s.13 applies: E is published as the primary model, with an
  addendum.

### B5. Hash and verify the runoff package

The script has already written `forecast_hash.txt`. Recompute it in the A7 format, then verify both packages:

```powershell
.venv\Scripts\python.exe -c "from pathlib import Path; from brfc.provenance import sha256_file; fz = Path('outputs/freeze_runoff'); fs = sorted(p for p in fz.rglob('*') if p.is_file() and p.name != 'forecast_hash.txt'); (fz / 'forecast_hash.txt').write_text(''.join(f'{sha256_file(p)}  {p.relative_to(fz).as_posix()}\n' for p in fs), encoding='utf-8', newline='\n')"
.venv\Scripts\python.exe -c "from pathlib import Path; from brfc.provenance import sha256_file; fz = Path('outputs/freeze_runoff'); rows = [x.split('  ', 1) for x in (fz / 'forecast_hash.txt').read_text(encoding='utf-8').splitlines() if x]; files = {p.relative_to(fz).as_posix() for p in fz.rglob('*') if p.is_file()} - {'forecast_hash.txt'}; bad = [n for h, n in rows if sha256_file(fz / n) != h]; print(len(rows), 'hashed;', 'unhashed:', sorted(files - {n for _, n in rows}), '; mismatched:', bad)"
Get-Content outputs\freeze_runoff\forecast_hash.txt
.venv\Scripts\python.exe -c "from pathlib import Path; from brfc.provenance import sha256_file; fz = Path('outputs/freeze'); rows = [x.split('  ', 1) for x in (fz / 'forecast_hash.txt').read_text(encoding='utf-8').splitlines() if x]; files = {p.relative_to(fz).as_posix() for p in fz.rglob('*') if p.is_file()} - {'forecast_hash.txt'}; bad = [n for h, n in rows if sha256_file(fz / n) != h]; print(len(rows), 'hashed;', 'unhashed:', sorted(files - {n for _, n in rows}), '; mismatched:', bad)"
git diff --stat outputs\freeze
```

- **Both verifications** must print `unhashed: [] ; mismatched: []`.
- **First-round package.** `git diff --stat outputs\freeze` must print nothing: the first-round package is
  unchanged.

### B6. Commit, tag, push (before 08:00 BRT on Sunday 2026-10-25)

Run the lines one at a time, as in A8.

```powershell
git add outputs data\interim data\SOURCES.lock.json data\manual
git status --short              # nothing under src\, scripts\ or tests\; nothing from data\raw\; nothing in outputs\freeze\
git commit -m "FINAL 2026 runoff forecast: freeze 2026-10-25T01:00:00Z"
if ($LASTEXITCODE) { throw "commit failed: do not tag" }
git ls-tree -r --name-only HEAD outputs/freeze_runoff  # lists every package file, including draws.npz
git tag -a freeze-2026-r2 -m "Runoff freeze 2026-10-25T01:00:00Z (outputs/freeze_runoff/forecast_hash.txt)"
if ((git rev-parse 'freeze-2026-r2^{commit}') -ne (git rev-parse HEAD)) { throw "tag is not on the freeze commit" }
git push origin main
git push origin freeze-2026-r2
```

- **Check the push.** The tag appears on GitHub and CI passes on the commit.
- **Scoring.** The runoff scorecard is later produced from `outputs\freeze_runoff\` and the TSE final runoff totals,
  whatever the result. See section C.

## C. Scoring (after the TSE count)

The rules are [`PREREG_ADDENDUM_06.md`](../PREREG_ADDENDUM_06.md). The scoring code was committed before the freeze
and is not changed after the result is known.

### C1. Enter the TSE count

- **When.** Once the TSE results site shows 100% of polling sections totalled for the round.
- **Read the page in a browser.** Open the TSE results site (`https://resultados.tse.jus.br/`), office Presidente,
  Brasil. Type every ballot candidate's votes, the blank and null votes and the valid total exactly as displayed.
  If a plain request to TSE's published results data succeeds, save the raw response with a provenance record. If
  it is refused, the browser reading is the source. Never work around a refusal.
- **Evidence.** Save a full-page screenshot as `data\raw\tse\results_2026_r<round>.png` (git-ignored). Hash it with
  `(Get-FileHash <file> -Algorithm SHA256).Hash.ToLower()`, and save the page with the Wayback Machine.
- **File.** `data\manual\results_2026.csv`, with the columns of `data\manual\results_secondary.csv`:
  - one row per ballot candidate, with `election` 2026 and the round;
  - `total_valid_votes`, `blank_votes` and `null_votes` as displayed;
  - the page URL in `source_url` and the reading time in `retrieved_utc`;
  - the screenshot hash and the archive URL in `notes`.
  Votes that TSE does not count as valid (for example "anulados sub judice") stay out of the valid total and are
  named in `notes`.

### C2. Score

```powershell
.venv\Scripts\python.exe scripts\score_2026.py --stage r1
.venv\Scripts\python.exe scripts\score_2026.py --stage president      # only if a candidate was elected outright
```

- **Checks first.** The script stops, and writes nothing, if either check fails:
  - `outputs\freeze\forecast_hash.txt` does not verify;
  - the result file fails the Addendum 06 s.2 checks: a ballot candidate missing, a name not on the ballot, or votes
    not summing to the valid total.
- **Files written.** `outputs\scorecard_2026_r1.json`, `.csv` and `_categories.csv`, and `docs\scorecard-2026.md`.
  The frozen package is only read.
- **After the runoff count.** Add the round-2 rows to `results_2026.csv`, then run `--stage runoff` and
  `--stage president`.

### C3. Commit and push

```powershell
git add data\manual\results_2026.csv outputs\scorecard_2026_* docs\scorecard-2026.md
git status --short              # nothing in outputs\freeze\ or outputs\freeze_runoff\; nothing under src\ or scripts\
git commit -m "2026 first-round scorecard (TSE count, 100% of sections)"
git push origin main
```

- **Post-mortem.** It is written afterwards, in its own file. It keeps pre-election information apart from
  post-election analysis and does not change the scorecard.

## If a step fails

| Failure | Action |
| --- | --- |
| Polymarket API unavailable at the freeze time | Retry. The CSV records the actual retrieval time. If no response is obtained, run A3 as `scripts\snapshot_benchmarks.py --label freeze --skip-polymarket`, so the PollingData rows still reach the package. The market benchmark is then N/A for this freeze; say so in the commit message. Never substitute a later price without saying so in the commit message and an addendum. |
| PollingData page unavailable | Baseline A is N/A for this freeze. Record it in the commit message. |
| Wikipedia revision at the freeze does not parse | PREREG s.13: use the last reliably parsed revision and disclose the gap. |
| F does not converge (after Addendum 03's third attempt) | PREREG s.13: E becomes the primary model, with an addendum. |
| A6 stops: `outputs\h2h_deviations.csv` or the first-round fit is missing | Addendum 04 s.7: freeze and push the first-round package without the president files. Publish the conditional forecast later as PRELIMINARY with its own timestamp. |
| `forecast_2026_runoff.py` stops before fitting | Read the message. The usual causes are a missing `--pair`, a name not on the 2026 ballot, a FINAL cutoff other than 2026-10-24, or a `--freeze-dir` holding the first-round package. Nothing is written. Correct the command and rerun B4. |
| `forecast_2026_runoff.py` stops: "skipped: 0 polls < 8" (or fewer than 8) | The poll table holds too few runoff polls of the pair fielded after the first round. Refresh (B0 or B3) and check `data\interim\polls_wiki_2026_runoff.csv`. At the freeze, if fewer than 8 exist, PREREG s.13 applies: report N/A with the poll count. |
| `forecast_2026_runoff.py` stops with "missing historical round-2 eve fits" | The historical round-2 fits are not in `data\cache\fits\`. Run `.venv\Scripts\python.exe scripts\run_backtest_fits.py --workers 6` (fits only cells that are not cached; do not use `make backtest`, whose `--repair` step refits cells that already used all three attempts). Then rerun B4. |
| An error found after the tag is pushed | New commit plus a dated addendum. The frozen files and the tag stay as they are. |
| `score_2026.py` stops: the package does not verify | Do not score. Find the change with `git diff freeze-2026-r1 -- outputs/freeze` and restore the tagged files with `git checkout freeze-2026-r1 -- outputs/freeze`. |
| `score_2026.py` stops: the result file fails a check | Correct the typed values against the TSE page. Never edit a value to make a check pass. |

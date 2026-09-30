# Reproduce everything from the committed data (see README "Reproduce").
PY = uv run python

# $(call require,VAR): stop with a message when a required variable is empty
require = $(if $(strip $($(1))),,$(error $(1) is required, e.g. make $@ $(1)=...))

.PHONY: setup lint test reparse readiness verify backtest sensitivity revision-2014 runoff-backtest forecast president runoff-2026 report figures all reproduce

setup:        ## install the locked environment
	uv sync --locked

lint:         ## ruff lint + format check
	uv run ruff check src scripts tests
	uv run ruff format --check src scripts tests

test:         ## validity test suite (leakage, information time, LOEO, prereg hash, provenance)
	uv run pytest -q

reparse:      ## re-parse every pinned revision and compare all values with the committed tables (restores them)
	$(PY) scripts/reparse_sources.py

readiness:    ## data-readiness gate (+ TSE reconciliation when the manual files are present)
	$(PY) scripts/check_readiness.py

verify:       ## 100% final-poll verification and the independent historical error table
	$(PY) scripts/verify_final_polls.py

backtest:     ## stage 1 fits (cached) + stage 2 LOEO evaluation
	$(PY) scripts/run_backtest_fits.py --workers 6
	$(PY) scripts/run_backtest_fits.py --workers 6 --repair
	$(PY) scripts/evaluate_backtest.py

sensitivity:  ## pre-registered sensitivity analyses (eve horizon); needs backtest
	$(PY) scripts/run_sensitivity.py --workers 6

revision-2014: ## labelled post-result revision of the 2014 first round (Addendum 04 s.3); needs backtest
	$(PY) scripts/run_revision_2014.py

runoff-backtest: ## pre-first-round head-to-head fits (cached) + evaluation; writes outputs/h2h_deviations.csv
	$(PY) scripts/run_runoff_backtest.py --stage fits --workers 4
	$(PY) scripts/run_runoff_backtest.py --stage evaluate

forecast:     ## 2026 forecast from the committed poll snapshot (STATUS=PRELIMINARY|FINAL CUTOFF=YYYY-MM-DD)
	$(call require,STATUS)
	$(call require,CUTOFF)
	$(PY) scripts/forecast_2026.py --status $(STATUS) --cutoff $(CUTOFF)

president:    ## 2026 probability of being elected (STATUS CUTOFF [FREEZE_DIR]); needs forecast + runoff-backtest
	$(call require,STATUS)
	$(call require,CUTOFF)
	$(PY) scripts/forecast_2026_president.py --status $(STATUS) --cutoff $(CUTOFF) $(if $(FREEZE_DIR),--freeze-dir $(FREEZE_DIR))

runoff-2026:  ## 2026 runoff after the first round (STATUS CUTOFF PAIR_A PAIR_B [FREEZE_DIR]); pair from the TSE count
	$(call require,STATUS)
	$(call require,CUTOFF)
	$(call require,PAIR_A)
	$(call require,PAIR_B)
	$(PY) scripts/forecast_2026_runoff.py --status $(STATUS) --cutoff $(CUTOFF) --pair "$(PAIR_A)" "$(PAIR_B)" $(if $(FREEZE_DIR),--freeze-dir $(FREEZE_DIR))

report:       ## regenerate docs/historical-backtest.md and docs/runoff-backtest.md from outputs/
	$(PY) scripts/build_report.py

figures:      ## rebuild every figure from outputs/ (the president figure once outputs/president_2026.json exists)
	$(PY) -c "from brfc import figures; figures.all_historical(); figures.all_2026()"

all: lint test readiness verify backtest runoff-backtest figures

reproduce:    ## everything from a clean clone, including all fits (several hours on 6 CPU cores)
	$(MAKE) setup lint test reparse readiness verify backtest sensitivity revision-2014 runoff-backtest
	$(MAKE) forecast STATUS=PRELIMINARY CUTOFF=2026-09-29
	$(MAKE) president STATUS=PRELIMINARY CUTOFF=2026-09-29
	$(MAKE) report figures

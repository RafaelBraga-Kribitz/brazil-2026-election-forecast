# Reproduce everything from the committed data (see README "Reproduce").
PY = uv run python

.PHONY: setup lint test readiness verify backtest forecast figures all

setup:        ## install the locked environment
	uv sync --locked

lint:         ## ruff lint + format check
	uv run ruff check src scripts tests
	uv run ruff format --check src scripts tests

test:         ## validity test suite (leakage, information time, LOEO, prereg hash, provenance)
	uv run pytest -q

readiness:    ## data-readiness gate (+ TSE reconciliation when the manual files are present)
	$(PY) scripts/check_readiness.py

verify:       ## 100% final-poll verification and the independent historical error table
	$(PY) scripts/verify_final_polls.py

backtest:     ## stage 1 fits (cached) + stage 2 LOEO evaluation
	$(PY) scripts/run_backtest_fits.py --workers 6
	$(PY) scripts/run_backtest_fits.py --workers 6 --repair
	$(PY) scripts/evaluate_backtest.py

forecast:     ## 2026 forecast from the committed poll snapshot (STATUS=PRELIMINARY|FINAL CUTOFF=YYYY-MM-DD)
	$(PY) scripts/forecast_2026.py --status $(STATUS) --cutoff $(CUTOFF)

figures:      ## rebuild every figure from outputs/
	$(PY) -c "from brfc import figures; figures.all_historical(); figures.all_2026()"

all: lint test readiness verify backtest figures

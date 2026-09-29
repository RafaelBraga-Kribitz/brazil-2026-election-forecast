# PREREG addendum 03: third sampling attempt for fits that fail the convergence criteria

**Date:** 2026-09-29 (UTC). Written after registration and before any backtest score, error or forecast was
computed. Only stage-1 sampler diagnostics had been seen.

## Change
Section 4 "Inference". Registered: if a fit fails the convergence criteria, refit with target_accept 0.99 and 2000
tuning steps; if it still fails, report it as non-converged. **Amended:** if the second attempt fails, make a third
attempt with target_accept 0.99, 3000 tuning steps and 3000 draws per chain. Only if the third attempt fails is the
fit reported as non-converged and flagged.

## Why
- **Which fits failed.** Four fits missed the registered bar after the second attempt: 2014 first round at T-14,
  T-7 and eve (12-16 polls), and 2018 runoff at T-7 (15 polls). Tail ESS was 160-360 against a threshold of 400;
  R-hat was at most 1.015; there were no divergences.
- **What the criteria measure.** They measure Monte Carlo reliability. Longer tuning and more draws target the same
  posterior with more precision. The model, the priors and the data are unchanged, so this choice cannot favour any
  forecast.
- **Timing.** The change was made before any fit had been scored.

## Effect
- Non-converged fits are refit from the third attempt (`scripts/run_backtest_fits.py --repair`).
- Every attempt's diagnostics are stored in the fit metadata and in `outputs/model_diagnostics.csv`.

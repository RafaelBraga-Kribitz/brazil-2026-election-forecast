"""Historical head-to-head (h2h) runoff and president backtest (PREREG_ADDENDUM_04).

Stage "fits" (polls only; never loads results): fits the h2h random walk of every pair listed by
brfc.runoff.h2h_pairs for each historical election at first-round horizons 1 (eve) and 7, production random-walk
variant from outputs/regime_selection.json. Fits are cached in data/cache/fits_h2h/ (brfc.runoff.H2H_CACHE, separate
from the first-round/runoff cache) and skipped if present.

Stage "repair" (polls only): refits, from the third attempt of Addendum 03, every cached untagged historical h2h fit
that is non-converged and has not yet run that attempt. A fit that fails the third attempt stays non-converged and
is flagged in every table that uses it.

Stage "evaluate" (reads official results): LOEO h2h election-day term, baselines B and C, scoring. Writes outputs/:
  runoff_backtest.csv             h2h scores of the actual runoff pair per (election, cutoff, model)
  runoff_backtest_categories.csv  per-candidate h2h forecasts, intervals and errors
  president_backtest.csv          probability of being elected per combination (F_E primary, F_F, E_E) and for the
                                  B and C baselines, scored against the elected candidate
  h2h_deviations.csv              h2h eve deviations (actual pair) used by the LOEO term and by the 2026 forecast
  h2h_error_models.csv            LOEO h2h error-model posteriors (which elections trained each)
  success_criteria_runoff.json    criteria HR1-HR3 of Addendum 04 s.5 (met / not met, with values)
  runoff_sensitivity.csv          h2h error-model scale prior 1.5 and 6 beside the registered 3 (reported only)

Usage:
  python scripts/run_runoff_backtest.py --stage fits [--workers 4] [--force] [--dry-run]
  python scripts/run_runoff_backtest.py --stage repair [--workers 4] [--dry-run]     (or --repair)
  python scripts/run_runoff_backtest.py --stage evaluate
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from brfc import config

OUT = config.OUTPUTS
REPAIR_START_ATTEMPT = 2  # 0-based index into brfc.model.ATTEMPTS: the third attempt (Addendum 03)


def production_variant() -> str:
    return json.loads((OUT / "regime_selection.json").read_text(encoding="utf-8"))["production_variant"]


def _job(args) -> str:
    e, pair, h, variant, force, start = args
    from brfc import runoff
    from brfc.data import load_polls

    t = time.time()
    f = runoff.fit_h2h(load_polls(config.HISTORICAL), e, tuple(pair), h, variant, force=force, start_attempt=start)
    status = "skipped" if f is None else f"{f.meta['n_polls']} polls, {f.meta.get('diagnostics')}"
    return f"{e} h2h {' vs '.join(pair)} r1h{h:02d} {variant}: {status} [{time.time() - t:.0f}s]"


def jobs(variant: str, force: bool = False, elections=config.HISTORICAL) -> list[tuple]:
    from brfc import runoff
    from brfc.data import load_polls

    polls = load_polls(config.HISTORICAL)
    out = []
    for e in elections:
        for h in runoff.H2H_HORIZONS:
            for pair in runoff.h2h_pairs(polls, e, runoff.h2h_cutoff(e, h)):
                out.append((e, pair, h, variant, force, 0))
    return out


def needs_repair(meta: dict) -> bool:
    """A cached untagged historical h2h fit that is non-converged and has not yet run the third attempt."""
    from brfc import model

    if meta.get("kind") != "h2h" or meta.get("status") != "ok" or meta.get("tag"):
        return False
    if str(meta.get("election")) not in config.HISTORICAL:
        return False
    if (meta.get("diagnostics") or {}).get("converged", True):
        return False
    done = max((int(a.get("attempt", 0)) for a in meta.get("attempts") or []), default=1)
    return done < len(model.ATTEMPTS)


def repair_jobs(cache: Path | None = None) -> list[tuple]:
    """Repair jobs (force, third attempt) for every h2h fit in `cache` (default brfc.runoff.H2H_CACHE) that needs it."""
    from brfc import runoff

    cache = Path(cache) if cache is not None else runoff.H2H_CACHE
    todo = []
    for p in sorted(cache.glob("*.json")):
        m = json.loads(p.read_text(encoding="utf-8"))
        if needs_repair(m):
            pair = tuple(m["pair"])
            todo.append((str(m["election"]), pair, int(m["r1_horizon"]), m["variant"], True, REPAIR_START_ATTEMPT))
    return todo


def run_fits(job_list: list[tuple], workers: int) -> None:
    if not job_list:
        return
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_job, j) for j in job_list]
        for fut in as_completed(futs):
            print(fut.result(), flush=True)


def evaluate(variant: str) -> None:
    from brfc import runoff
    from brfc.data import load_polls, load_results

    results, polls = load_results(), load_polls(config.HISTORICAL)
    out = runoff.evaluate_backtest(variant, results, polls=polls)
    OUT.mkdir(exist_ok=True)
    out["runoff"].to_csv(OUT / "runoff_backtest.csv", index=False)
    out["runoff_categories"].to_csv(OUT / "runoff_backtest_categories.csv", index=False)
    out["president"].to_csv(OUT / "president_backtest.csv", index=False)
    out["deviations"].to_csv(OUT / "h2h_deviations.csv", index=False)
    out["error_models"].to_csv(OUT / "h2h_error_models.csv", index=False)
    crit = runoff.success_criteria(out) | {"variant": variant}
    (OUT / "success_criteria_runoff.json").write_text(
        json.dumps(crit, indent=1, ensure_ascii=False, default=float) + "\n", encoding="utf-8"
    )
    sens = runoff.sensitivity_backtest(variant, results, base=out, polls=polls)
    sens.to_csv(OUT / "runoff_sensitivity.csv", index=False)

    cols = ["election", "r1_horizon", "model", "status", "mae", "margin_error", "coverage_80", "coverage_94"]
    r = out["runoff"]
    print(r[[c for c in cols if c in r.columns]].to_string(index=False))
    p = out["president"]
    cols = ["election", "r1_horizon", "model", "status", "p_winner", "brier", "log", "unmodelled"]
    print(p[[c for c in cols if c in p.columns]].to_string(index=False))
    elections = sorted(out["deviations"]["election"].unique())
    print(f"h2h deviation table: {len(elections)} elections ({', '.join(elections) or 'none'})")
    for k, v in crit.items():
        if isinstance(v, dict) and "result" in v:
            print(f"{k}: {v['result']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["fits", "repair", "evaluate"])
    ap.add_argument("--repair", action="store_true", help="same as --stage repair")
    ap.add_argument("--variant", default=None, help="default: outputs/regime_selection.json production variant")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true", help="refit even when a cached fit exists")
    ap.add_argument("--dry-run", action="store_true", help="list the fits without running them")
    a = ap.parse_args()
    stage = "repair" if a.repair else a.stage
    if stage is None:
        ap.error("--stage is required (fits, repair or evaluate)")
    if stage == "repair":
        todo = repair_jobs()
        print(f"repairing {len(todo)} h2h fits from attempt {REPAIR_START_ATTEMPT + 1}", flush=True)
        if a.dry_run:
            for e, pair, h, variant, *_ in todo:
                print(f"  {e} r1h{h:02d} {' vs '.join(pair)} {variant}")
            return
        run_fits(todo, a.workers)
        return
    variant = a.variant or production_variant()
    if stage == "fits":
        todo = jobs(variant, a.force)
        print(f"{len(todo)} h2h fits ({variant})", flush=True)
        if a.dry_run:
            from brfc import runoff

            for e, pair, h, *_ in todo:
                cached = (runoff.H2H_CACHE / f"{runoff.h2h_key(e, pair, h, variant)}.json").exists()
                print(f"  {e} r1h{h:02d} {' vs '.join(pair)}{' (cached)' if cached else ''}")
            return
        run_fits(todo, a.workers)
        return
    evaluate(variant)


if __name__ == "__main__":
    main()

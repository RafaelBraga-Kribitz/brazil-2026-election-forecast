"""Stage 1 of the historical backtest: fit the random walk at every (election, round, horizon, variant).

Uses polls only; never loads election results. Fits are cached in data/cache/fits/ and skipped if present.
Usage: python scripts/run_backtest_fits.py [--variants two_regime single_regime] [--workers 6] [--force]
"""

from __future__ import annotations

import argparse
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

from brfc import config


def _job(args):
    e, r, h, variant, force, tag, priors, dep, minp = args
    from brfc.data import load_polls
    from brfc.pipeline import fit_one

    t = time.time()
    f = fit_one(load_polls(config.HISTORICAL), e, r, h, variant, force=force, tag=tag, priors=priors,
                dependence_rule=dep, min_polls_per_pollster=minp)
    status = "skipped" if f is None else f"{f.meta['n_polls']} polls, {f.meta.get('diagnostics')}"
    return f"{e} r{r} h{h:02d} {variant} {tag}: {status} [{time.time() - t:.0f}s]"


def jobs(variants, elections, horizons_r1=config.HORIZONS_R1, horizons_r2=config.HORIZONS_R2, **kw):
    out = []
    for variant in variants:
        for e in elections:
            for r, hs in ((1, horizons_r1), (2, horizons_r2)):
                for h in sorted(hs):
                    out.append((e, r, h, variant, kw.get("force", False), kw.get("tag", ""), kw.get("priors"),
                                kw.get("dependence_rule", True), kw.get("min_polls_per_pollster", 1)))
    return out


def run(job_list, workers: int) -> None:
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_job, j) for j in job_list]
        for fut in as_completed(futs):
            print(fut.result(), flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", nargs="+", default=["two_regime", "single_regime"])
    ap.add_argument("--elections", nargs="+", default=list(config.HISTORICAL))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    run(jobs(a.variants, a.elections, force=a.force), a.workers)


if __name__ == "__main__":
    main()

"""Stage 1 of the historical backtest: fit the random walk at every (election, round, horizon, variant).

Uses polls only; never loads election results. Fits are cached in data/cache/fits/ and skipped if present.
Usage: python scripts/run_backtest_fits.py [--variants two_regime single_regime] [--force]
"""

from __future__ import annotations

import argparse
import time

from brfc import config
from brfc.data import load_polls
from brfc.pipeline import fit_one


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", nargs="+", default=["two_regime", "single_regime"])
    ap.add_argument("--elections", nargs="+", default=list(config.HISTORICAL))
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    polls = load_polls(tuple(a.elections))
    for variant in a.variants:
        for e in a.elections:
            for r, hs in ((1, config.HORIZONS_R1), (2, config.HORIZONS_R2)):
                for h in sorted(hs):  # eve first: it feeds the election-day term
                    t = time.time()
                    f = fit_one(polls, e, r, h, variant, force=a.force)
                    status = "skipped" if f is None else f"{f.meta['n_polls']} polls, {f.meta.get('diagnostics')}"
                    print(f"{e} r{r} h{h:02d} {variant}: {status} [{time.time() - t:.0f}s]", flush=True)


if __name__ == "__main__":
    main()

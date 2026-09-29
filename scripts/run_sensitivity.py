"""Pre-registered sensitivity analyses (PREREG.md s.10) on the eve-horizon historical backtest.

Never used to choose the headline model. Requires outputs/regime_selection.json (production RW variant).
Writes outputs/sensitivity.csv (one row per setting x model x round type).
"""

from __future__ import annotations

import argparse
import json

import pandas as pd

from brfc import config
from brfc.data import load_results
from brfc.pipeline import evaluate_backtest

RW_SETTINGS = {  # tag -> fit_one keyword overrides (stage 1 refits at the eve horizon)
    "ns1": {"priors": {"sigma_ns": 1.0}},
    "ns4": {"priors": {"sigma_ns": 4.0}},
    "leader": {"allocation": "leader_weighted"},
    "min3": {"min_polls_per_pollster": 3},
    "nodep": {"dependence_rule": False},
    "final14": {"final_days": 14},
}
ERROR_SETTINGS = {  # stage 2 only
    "tau1.5": {"tau": 1.5},
    "tau6": {"tau": 6.0},
    "sig1.5": {"sigma_scale": 1.5},
    "sig6": {"sigma_scale": 6.0},
}


def _fit(args):
    e, r, variant, tag, kw = args
    from brfc.data import load_polls
    from brfc.pipeline import fit_one

    f = fit_one(load_polls(config.HISTORICAL), e, r, 1, variant, tag=tag, **kw)
    return f"{tag} {e} r{r}: {'skipped' if f is None else f.meta['diagnostics']['converged']}"


def summarise(scores: pd.DataFrame, setting: str) -> pd.DataFrame:
    s = scores[(scores["horizon"] == 1) & scores["mae"].notna()].copy()
    s["round_type"] = s["round"].map({1: "first round", 2: "runoff"})
    g = s.groupby(["model", "round_type"]).agg(
        n_rounds=("mae", "size"),
        mae=("mae", "mean"),
        margin_abs_error=("margin_abs_error", "mean"),
        coverage_80=("coverage_80", "mean"),
        coverage_94=("coverage_94", "mean"),
        brier_first=("brier_first", "mean"),
    )
    return g.reset_index().assign(setting=setting)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    variant = json.loads((config.OUTPUTS / "regime_selection.json").read_text())["production_variant"]
    other = "single_regime" if variant == "two_regime" else "two_regime"
    jobs = [(e, r, variant, tag, kw) for tag, kw in RW_SETTINGS.items() for e in config.HISTORICAL for r in (1, 2)]
    from concurrent.futures import ProcessPoolExecutor

    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for line in ex.map(_fit, jobs):
            print(line, flush=True)
    res = load_results()
    rows = [
        summarise(evaluate_backtest(variant, res)[0], "primary"),
        summarise(evaluate_backtest(other, res)[0], f"regime={other}"),
    ]
    for tag in RW_SETTINGS:
        rows.append(summarise(evaluate_backtest(variant, res, tag=tag)[0], tag))
    for name, prior in ERROR_SETTINGS.items():
        rows.append(summarise(evaluate_backtest(variant, res, error_prior=prior)[0], name))
    out = pd.concat(rows, ignore_index=True)
    out.to_csv(config.OUTPUTS / "sensitivity.csv", index=False)
    print(out[out["model"] == "F"].round(3).to_string(index=False))


if __name__ == "__main__":
    main()

"""2026 first-round forecast: same code path as the backtest, trained on all three historical elections.

Uses 2026 polls up to the cutoff and historical (2014/2018/2022) results only. 2026 results are never read here.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from brfc import baselines, config, election_day
from brfc.pipeline import (
    POINT_BASELINES,
    baseline_errors,
    baseline_point,
    eve_deviations,
    fit_one,
    historical_fits,
    latent_forecast,
)
from brfc.provenance import sha256_text_file_lf

ELECTION = "2026"


def forecast_tag(status: str, rev: dict, corrections: Path = config.DATA / "manual" / "corrections.csv") -> str:
    """Cache tag of a 2026 fit: the status, both Wikipedia revisions and the corrections file. A fit is reused only
    for the same information; a new EN revision or a new correction row gives a new key, hence a refit."""
    c = sha256_text_file_lf(corrections)[:8] if Path(corrections).exists() else "none"
    return f"{status.lower()}_pt{rev['pt_oldid']}_en{rev.get('en_oldid', 'none')}_c{c}"


def nearest_horizon(h: int) -> int:
    return min(config.HORIZONS_R1, key=lambda x: (abs(x - h), -x))


def summarise_draws(draws: np.ndarray, cats: list[str]) -> pd.DataFrame:
    named = [c for c in cats if c != config.OTHERS_LABEL]
    ni = [cats.index(c) for c in named]
    first = np.array(named)[np.argmax(draws[:, ni], axis=1)]
    order = np.argsort(-draws[:, ni], axis=1)
    top2 = np.zeros((draws.shape[0], len(named)), dtype=bool)
    for k in range(2):
        top2[np.arange(draws.shape[0]), order[:, k]] = True
    rows = []
    for k, c in enumerate(cats):
        x = draws[:, k]
        r = {
            "category": c,
            "mean": x.mean(),
            "median": np.median(x),
            "q03": np.quantile(x, 0.03),
            "q10": np.quantile(x, 0.10),
            "q90": np.quantile(x, 0.90),
            "q97": np.quantile(x, 0.97),
        }
        if c in named:
            j = named.index(c)
            r["p_first"] = float(np.mean(first == c))
            r["p_top_two"] = float(np.mean(top2[:, j]))
            r["p_above_50"] = float(np.mean(x > 50.0))
        rows.append(r)
    return pd.DataFrame(rows)


def run(
    polls_2026: pd.DataFrame,
    results_hist: pd.DataFrame,
    cutoff: date,
    *,
    variant: str,
    tag: str,
    error_prior: dict | None = None,
    rw_priors: dict | None = None,
) -> dict:
    """Returns a dict of DataFrames/values; caller writes the artefacts."""
    if set(results_hist["election"]) - set(config.HISTORICAL):
        raise ValueError("only historical results may be passed to the 2026 forecast")
    e_day = config.ELECTION_DATES[(ELECTION, 1)]
    h = (e_day - cutoff).days
    fit = fit_one(polls_2026, ELECTION, 1, h, variant, tag=tag, priors=rw_priors)
    if fit is None:
        raise RuntimeError("2026 fit skipped: too few polls")
    latent, cats = latent_forecast(fit, ELECTION)
    mean = dict(zip(cats, latent.mean(axis=0), strict=True))
    roles = election_day.assign_roles(cats, mean, 1)

    hist = historical_fits(variant)
    devs = eve_deviations({(e, r): f for (e, r, hh), f in hist.items() if hh == 1}, results_hist)
    train = election_day.loeo_training_set(devs, ELECTION, 1)
    out = {
        "fit": fit,
        "categories": cats,
        "roles": roles,
        "horizon": h,
        "cutoff": cutoff,
        "variant": variant,
        "train_elections": sorted(train["election"].unique().tolist()),
        "draws": {"E0": latent},
        "error_models": {},
    }
    for v in ("E", "F"):
        post = election_day.fit_error_model(
            train, v, ["rank1", "rank2", "rest"], seed=20261004 + (v == "F"), prior=error_prior
        )
        out["error_models"][v] = post.summary()
        out["draws"][v] = election_day.apply(fit.draws, fit.meta["series"], roles, post, 1, seed=20261004)
    out["summaries"] = {m: summarise_draws(d, cats) for m, d in out["draws"].items()}

    berr = baseline_errors(hist, results_hist)
    hh = nearest_horizon(h)
    rows = []
    for b in POINT_BASELINES:
        p = baseline_point(b, fit, ELECTION, cutoff)
        rmse = baselines.loeo_rmse(berr, b, ELECTION, 1, hh)
        row = {
            "baseline": b,
            "cutoff": str(cutoff),
            "error_horizon_used": hh,
            "loeo_rmse": rmse,
            "status": "N/A: no qualifying poll in the 14 days to cutoff" if p is None else "ok",
        }
        if p is not None:
            row |= {f"point_{c}": p[c] for c in cats}
            if rmse is not None:
                d = baselines.probabilistic(p, cats, rmse, 1, seed=7)
                out["draws"][b] = d
                s = summarise_draws(d, cats).set_index("category")
                row |= {f"p_first_{c}": s.loc[c, "p_first"] for c in cats if c != config.OTHERS_LABEL}
        rows.append(row)
    out["baselines"] = pd.DataFrame(rows)
    return out

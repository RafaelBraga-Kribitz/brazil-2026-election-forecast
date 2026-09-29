"""Two-stage pipeline.

Stage 1 (`fit_one`, expensive): information set at a cutoff -> random-walk posterior -> cached draws.
          Never touches election results.
Stage 2 (`evaluate_backtest`, cheap): LOEO election-day term, baselines, scoring against official results.
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from brfc import baselines, config, election_day, model, scoring
from brfc.transform import cutoff_for, polls_for_forecast, prepare, window_start

CACHE = config.DATA / "cache" / "fits"
VARIANTS = {"two_regime": True, "single_regime": False}


def fit_key(election: str, round_: int, horizon: int, variant: str, tag: str = "") -> str:
    return f"{election}_r{round_}_h{horizon:02d}_{variant}{('_' + tag) if tag else ''}"


@dataclass
class CachedFit:
    key: str
    meta: dict
    draws: np.ndarray  # (N, S) latent election-day draws
    kept_rows: pd.DataFrame  # long valid-vote rows in the information set (after dependence rule)


def information_set(
    polls: pd.DataFrame,
    election: str,
    round_: int,
    horizon: int,
    *,
    dependence_rule: bool = True,
    min_polls_per_pollster: int = 1,
    allocation: str = "proportional",
    final_days: int | None = None,
):
    valid = prepare(polls, election, round_, allocation)
    cutoff = cutoff_for(election, round_, horizon)
    if final_days:  # sensitivity: only polls with fieldwork ending in the final `final_days` days before cutoff
        from datetime import timedelta

        valid = valid[pd.to_datetime(valid["field_end"]).dt.date > cutoff - timedelta(days=final_days)]
    named = [config.RUNOFF_PAIRS[election][0]] if round_ == 2 else None
    if min_polls_per_pollster > 1:
        from brfc.transform import available_at

        counts = available_at(valid, cutoff).groupby("pollster")["poll_id"].nunique()
        valid = valid[valid["pollster"].isin(counts[counts >= min_polls_per_pollster].index)]
    if not dependence_rule:
        from brfc.transform import available_at, choose_named, series_table

        avail = available_at(valid, cutoff)
        named = named or choose_named(avail, cutoff)
        return series_table(avail, named), named, avail.iloc[0:0], avail, cutoff
    wide, named, dropped, kept = polls_for_forecast(valid, cutoff, named)
    return wide, named, dropped, kept, cutoff


def fit_one(
    polls: pd.DataFrame,
    election: str,
    round_: int,
    horizon: int,
    variant: str = "two_regime",
    *,
    tag: str = "",
    priors: dict | None = None,
    sampler: dict | None = None,
    dependence_rule: bool = True,
    min_polls_per_pollster: int = 1,
    force: bool = False,
    cache: Path = CACHE,
    start_attempt: int = 0,
    allocation: str = "proportional",
    final_days: int | None = None,
) -> CachedFit | None:
    key = fit_key(election, round_, horizon, variant, tag)
    cache.mkdir(parents=True, exist_ok=True)
    if not force and (cache / f"{key}.json").exists():
        return load_fit(key, cache)
    wide, named, dropped, kept, cutoff = information_set(
        polls,
        election,
        round_,
        horizon,
        dependence_rule=dependence_rule,
        min_polls_per_pollster=min_polls_per_pollster,
        allocation=allocation,
        final_days=final_days,
    )
    series = named + ([config.OTHERS_LABEL] if len(named) > 1 else [])
    n_polls = int(wide["poll_id"].nunique())
    meta = {
        "key": key,
        "election": election,
        "round": round_,
        "horizon": horizon,
        "variant": variant,
        "tag": tag,
        "cutoff": str(cutoff),
        "named": named,
        "series": series,
        "n_polls": n_polls,
        "n_pollsters": int(wide["pollster"].nunique()),
        "n_dropped_overlap": len(dropped),
        "max_field_end": str(wide["field_end"].max()) if n_polls else None,
        "poll_ids": sorted(wide["poll_id"].tolist()),
        "priors": priors or {},
        "options": {
            "dependence_rule": dependence_rule,
            "min_polls_per_pollster": min_polls_per_pollster,
            "allocation": allocation,
            "final_days": final_days,
        },
        "status": "ok",
    }
    if n_polls < config.MIN_POLLS_PER_FIT:
        meta["status"] = f"skipped: {n_polls} polls < {config.MIN_POLLS_PER_FIT}"
        (cache / f"{key}.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        return None
    kw = {
        "window_start": window_start(election, round_)
        if not final_days
        else max(window_start(election, round_), cutoff - timedelta(days=final_days - 1)),
        "election_day": config.ELECTION_DATES[(election, round_)],
        "cutoff": cutoff,
        "two_regime": VARIANTS[variant],
        "priors": priors,
    }
    attempts = []
    for k in range(start_attempt, len(model.ATTEMPTS)):  # pre-registered retries (PREREG s.4, Addendum 03)
        fr = model.fit(wide, series, sampler={**(sampler or {}), **model.ATTEMPTS[k]}, **kw)
        attempts.append({"attempt": k + 1, **fr.diagnostics})
        if fr.diagnostics["converged"]:
            break
    meta["attempts"] = attempts
    meta["diagnostics"] = fr.diagnostics
    np.save(cache / f"{key}.npy", fr.election_day_draws.astype(np.float32))
    fr.path.to_csv(cache / f"{key}.path.csv", index=False)
    fr.house.to_csv(cache / f"{key}.house.csv", index=False)
    fr.params.to_csv(cache / f"{key}.params.csv", index=False)
    kept.to_csv(cache / f"{key}.rows.csv", index=False)
    (cache / f"{key}.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return load_fit(key, cache)  # stored float32 draws: a fresh run equals a cached run bit for bit


def load_fit(key: str, cache: Path = CACHE) -> CachedFit | None:
    meta = json.loads((cache / f"{key}.json").read_text(encoding="utf-8"))
    if meta["status"] != "ok":
        return None
    draws = np.load(cache / f"{key}.npy").astype(float)
    rows = pd.read_csv(cache / f"{key}.rows.csv", dtype={"election": str, "publication_date": str})
    rows["publication_date"] = rows["publication_date"].fillna("")
    return CachedFit(key, meta, draws, rows)


def categories_of(meta: dict, election: str) -> list[str]:
    if meta["round"] == 2:
        return list(config.RUNOFF_PAIRS[election])
    return meta["series"]


def latent_forecast(fit: CachedFit, election: str) -> tuple[np.ndarray, list[str]]:
    """Latent-only forecast draws on the scoring categories (simplex-projected), no election-day term."""
    cats = categories_of(fit.meta, election)
    return election_day.apply(fit.draws, fit.meta["series"], {}, None, fit.meta["round"]), cats


def eve_deviations(fits: dict[tuple[str, int], CachedFit], results: pd.DataFrame) -> pd.DataFrame:
    """Observed-minus-forecast deviations at the eve horizon, with forecast-rank roles."""
    from brfc.data import actual_shares

    rows = []
    for (e, r), f in fits.items():
        draws, cats = latent_forecast(f, e)
        mean = dict(zip(cats, draws.mean(axis=0), strict=True))
        roles = election_day.assign_roles(cats, mean, r)
        act = actual_shares(results, e, r, cats)
        for c in cats:
            if c in roles:
                rows.append(
                    {
                        "election": e,
                        "round": r,
                        "category": c,
                        "role": roles[c],
                        "forecast": mean[c],
                        "actual": act[c],
                        "deviation": act[c] - mean[c],
                    }
                )
    return pd.DataFrame(rows)


MODEL_LABELS = {
    "F": "F: Bayesian RW + round-specific election-day term (primary)",
    "E": "E: Bayesian RW, zero-mean election-day error",
    "E0": "E0: Bayesian RW latent only (no election-day error; diagnostic)",
    "B": "B: 14-day latest-per-pollster average",
    "C": "C: Final Datafolha",
    "D": "D: Final AtlasIntel",
}
POINT_BASELINES = {"B": None, "C": "Datafolha", "D": "AtlasIntel"}


def baseline_point(kind: str, fit: CachedFit, election: str, cutoff) -> dict | None:
    named, r = fit.meta["named"], fit.meta["round"]
    if kind == "B":
        p = baselines.latest_per_pollster(fit.kept_rows, named, cutoff, r)
    else:
        p = baselines.final_poll_of(fit.kept_rows, named, cutoff, POINT_BASELINES[kind], r)
    return baselines.as_categories(p, categories_of(fit.meta, election), r) if p else None


def historical_fits(variant: str, tag: str = "", elections=config.HISTORICAL) -> dict[tuple[str, int, int], CachedFit]:
    out = {}
    for e in elections:
        for r, hs in ((1, config.HORIZONS_R1), (2, config.HORIZONS_R2)):
            for h in hs:
                k = fit_key(e, r, h, variant, tag)
                if (CACHE / f"{k}.json").exists():
                    f = load_fit(k)
                    if f is not None:
                        out[(e, r, h)] = f
    return out


def baseline_errors(fits_all: dict, results: pd.DataFrame) -> pd.DataFrame:
    """Observed-minus-point errors of every point baseline in every historical cell (filtered LOEO at use)."""
    from datetime import date

    from brfc.data import actual_shares

    berr = []
    for (e, r, h), f in fits_all.items():
        cats = categories_of(f.meta, e)
        act = actual_shares(results, e, r, cats)
        for b in POINT_BASELINES:
            p = baseline_point(b, f, e, date.fromisoformat(f.meta["cutoff"]))
            if p:
                berr += [
                    {"baseline": b, "election": e, "round": r, "horizon": h, "category": c, "error": act[c] - p[c]}
                    for c in cats
                ]
    return pd.DataFrame(berr, columns=["baseline", "election", "round", "horizon", "category", "error"])


def evaluate_backtest(
    variant: str, results: pd.DataFrame, *, elections=config.HISTORICAL, error_prior=None, tag: str = ""
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Returns (scores, category_rows, deviations, error_model_summaries) for one RW variant."""
    from datetime import date

    from brfc.data import actual_shares

    fits_all = historical_fits(variant, tag, elections)
    eve = {(e, r): f for (e, r, h), f in fits_all.items() if h == 1}
    devs = eve_deviations(eve, results)
    berr = baseline_errors(fits_all, results)

    scores, cat_rows, err_summ = [], [], []
    for (e, r, h), f in sorted(fits_all.items()):
        cats = categories_of(f.meta, e)
        act = actual_shares(results, e, r, cats)
        latent, _ = latent_forecast(f, e)
        mean = dict(zip(cats, latent.mean(axis=0), strict=True))
        roles = election_day.assign_roles(cats, mean, r)
        role_list = ["rank1"] if r == 2 else ["rank1", "rank2", "rest"]
        train = election_day.loeo_training_set(devs, e, r)
        base = {
            "election": e,
            "round": r,
            "horizon": h,
            "cutoff": f.meta["cutoff"],
            "variant": variant,
            "n_polls": f.meta["n_polls"],
            "categories": "|".join(cats),
        }
        forecasts = {"E0": latent}
        for v in ("E", "F"):
            post = election_day.fit_error_model(
                train, v, role_list, seed=zlib.crc32(f"{e}|{r}|{h}|{v}".encode()), prior=error_prior
            )
            forecasts[v] = election_day.apply(f.draws, f.meta["series"], roles, post, r, seed=1)
            err_summ.append(base | {"model": v} | post.summary())
        for m, d in forecasts.items():
            s = scoring.score(d, None, cats, act, r)
            scores.append(
                base
                | {"model": m, "train_elections": "+".join(sorted(set(config.HISTORICAL) - {e})) if m != "E0" else ""}
                | s
            )
            cat_rows += [base | {"model": m} | x for x in scoring.category_rows(d, None, cats, act)]
        for b in POINT_BASELINES:
            p = baseline_point(b, f, e, date.fromisoformat(f.meta["cutoff"]))
            if not p:
                scores.append(base | {"model": b, "status": "N/A: no qualifying poll in the 14 days to cutoff"})
                continue
            rmse = baselines.loeo_rmse(berr, b, e, r, h)
            if rmse is None:
                s = scoring.score(None, p, cats, act, r) | {"status": "point only: no LOEO error history"}
                scores.append(base | {"model": b} | s)
                cat_rows += [base | {"model": b} | x for x in scoring.category_rows(None, p, cats, act)]
                continue
            d = baselines.probabilistic(p, cats, rmse, r, seed=2)
            s = scoring.score(d, None, cats, act, r)
            s["mae"] = scoring.score(None, p, cats, act, r)["mae"]  # point MAE, not the conversion's mean
            pm = scoring.score(None, p, cats, act, r)
            s["margin_error"], s["margin_abs_error"], s["margin_pred"] = (
                pm["margin_error"],
                pm["margin_abs_error"],
                pm["margin_pred"],
            )
            train_e = sorted(
                berr.loc[
                    (berr["baseline"] == b) & (berr["round"] == r) & (berr["horizon"] == h) & (berr["election"] != e),
                    "election",
                ].unique()
            )
            scores.append(
                base
                | {
                    "model": b,
                    "loeo_rmse": rmse,
                    "train_elections": "+".join(train_e),
                    "status": "calibrated probabilistic conversion of point baseline",
                }
                | s
            )
            for x in scoring.category_rows(d, None, cats, act):  # point value as the estimate, conversion intervals
                x["mean"], x["error"] = p[x["category"]], p[x["category"]] - act[x["category"]]
                cat_rows.append(base | {"model": b} | x)
    return pd.DataFrame(scores), pd.DataFrame(cat_rows), devs, pd.DataFrame(err_summ)

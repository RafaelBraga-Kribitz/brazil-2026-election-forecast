"""Bayesian poll-aggregation model: independent valid-share Gaussian random walks, one per series.

For series s (a named candidate, or the pooled "Others") on day t:

    mu[s, 0]   ~ Normal(m0[s], 10)                      m0 = mean of the series' first-week polls
    mu[s, t]   = mu[s, t-1] + sigma[s, t] * z[s, t]      z ~ Normal(0, 1)   (non-centred walk)
    sigma[s,t] = sigma_rw[s] * (rho[s] if t is in the final LATE_REGIME_DAYS else 1)   (two-regime variant)
    y_i        ~ Normal(mu[s, t_i] + house[s, pollster_i], sqrt(sampling_var_i + sigma_ns[s]^2))
    sampling_var_i = 100^2 * p_i (1 - p_i) / n_i        (simple random sampling reference, deff = 1)
    house[s, :] = sigma_house[s] * ZeroSumNormal         (sums to zero over pollsters in the fit)

Priors: sigma_rw ~ HalfNormal(0.5) pp/day, rho ~ LogNormal(0, 0.75), sigma_house ~ HalfNormal(2),
sigma_ns ~ HalfNormal(2). The walk runs to election day; days after the information cutoff carry no data,
so their uncertainty comes only from the walk. The latent state is the average-pollster consensus: the
industry-wide election-day deviation is handled separately (brfc.election_day).

No function in this module accepts election results (tested in tests/test_leakage.py).
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from brfc import config

PRIORS = {"sigma_rw": 0.5, "rho_log_sd": 0.75, "sigma_house": 2.0, "sigma_ns": 2.0, "init_sd": 10.0}


def converged(diag: dict) -> bool:
    """Pre-registered convergence criteria (PREREG.md section 4)."""
    return (diag["rhat_max"] <= 1.01 and diag["ess_bulk_min"] >= 400 and diag["ess_tail_min"] >= 400
            and diag["divergences"] <= 0.01 * diag["draws"])


RETRY_SAMPLER = {"target_accept": 0.99, "tune": 2000}


@dataclass
class FitResult:
    series: list[str]
    days: list[date]
    election_day_draws: np.ndarray  # (n_draws, S) latent consensus valid share on election day, pp
    path: pd.DataFrame  # date, series, mean, q10, q90, q03, q97
    house: pd.DataFrame  # series, pollster, mean, q03, q97, n_polls
    params: pd.DataFrame  # series, param, mean, q03, q97
    diagnostics: dict
    n_polls: int
    cutoff: date
    variant: str
    polls: pd.DataFrame = field(repr=False, default_factory=pd.DataFrame)


def _long_obs(wide: pd.DataFrame, series: list[str], start: date, pollsters: list[str]) -> dict:
    rows = []
    for s_idx, s in enumerate(series):
        sub = wide[["field_mid", "pollster", "sample_size", s]].dropna(subset=[s])
        for r in sub.itertuples(index=False):
            rows.append((s_idx, (r.field_mid - start).days, pollsters.index(r.pollster), float(r.sample_size), float(r[3])))
    arr = np.array(rows, dtype=float)
    p = np.clip(arr[:, 4] / 100.0, 0.005, 0.995)
    return {
        "s": arr[:, 0].astype(int),
        "t": arr[:, 1].astype(int),
        "p": arr[:, 2].astype(int),
        "y": arr[:, 4],
        "samp_var": 1e4 * p * (1 - p) / arr[:, 3],
    }


def fit(
    wide: pd.DataFrame,
    series: list[str],
    *,
    window_start: date,
    election_day: date,
    cutoff: date,
    two_regime: bool = True,
    priors: dict | None = None,
    sampler: dict | None = None,
) -> FitResult:
    """Fit the random-walk model to the polls in `wide` (already restricted to the information set at `cutoff`)."""
    import arviz as az
    import pymc as pm

    pr = {**PRIORS, **(priors or {})}
    sk = {**config.SAMPLER, **(sampler or {})}
    wide = wide.copy()
    fm = pd.to_datetime(wide["field_mid"]).dt.date
    if (pd.to_datetime(wide["field_end"]).dt.date > cutoff).any():
        raise ValueError("information leak: poll with field_end after cutoff passed to fit()")
    wide["field_mid"] = [max(window_start, min(d, cutoff)) for d in fm]

    days = [window_start + timedelta(days=i) for i in range((election_day - window_start).days + 1)]
    T, S = len(days), len(series)
    pollsters = sorted(wide["pollster"].unique())
    P = len(pollsters)
    obs = _long_obs(wide, series, window_start, pollsters)

    m0 = np.array(
        [np.nanmean(wide.loc[wide["field_mid"] <= window_start + timedelta(days=13), s].to_numpy(dtype=float))
         if wide.loc[wide["field_mid"] <= window_start + timedelta(days=13), s].notna().any()
         else np.nanmean(wide[s].to_numpy(dtype=float)) for s in series]
    )
    late = np.zeros(T - 1)
    late[-config.LATE_REGIME_DAYS:] = 1.0  # innovations entering the final LATE_REGIME_DAYS days

    with pm.Model() as model:
        sigma_rw = pm.HalfNormal("sigma_rw", pr["sigma_rw"], shape=S)
        if two_regime:
            rho = pm.LogNormal("rho", 0.0, pr["rho_log_sd"], shape=S)
            scale = sigma_rw[:, None] * (1.0 + (rho[:, None] - 1.0) * late[None, :])
        else:
            scale = sigma_rw[:, None] * np.ones((1, T - 1))
        init = pm.Normal("init", m0, pr["init_sd"], shape=S)
        z = pm.Normal("z", 0.0, 1.0, shape=(S, T - 1))
        steps = pm.math.concatenate([init[:, None], scale * z], axis=1)
        mu = pm.Deterministic("mu", pm.math.cumsum(steps, axis=1))
        sigma_ns = pm.HalfNormal("sigma_ns", pr["sigma_ns"], shape=S)
        loc = mu[obs["s"], obs["t"]]
        if P > 1:
            sigma_house = pm.HalfNormal("sigma_house", pr["sigma_house"], shape=S)
            hz = pm.ZeroSumNormal("house_z", sigma=1.0, shape=(S, P), n_zerosum_axes=1)
            house = pm.Deterministic("house", sigma_house[:, None] * hz)
            loc = loc + house[obs["s"], obs["p"]]
        pm.Normal("y", loc, pm.math.sqrt(obs["samp_var"] + sigma_ns[obs["s"]] ** 2), observed=obs["y"])

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            idata = pm.sample(
                draws=sk["draws"], tune=sk["tune"], chains=sk["chains"], cores=1, target_accept=sk["target_accept"],
                random_seed=sk["random_seed"], progressbar=False, compile_kwargs={"mode": "NUMBA"},
            )

    post = idata.posterior
    mu_d = post["mu"].stack(sample=("chain", "draw")).transpose("sample", ...).to_numpy()  # (N, S, T)
    ed = mu_d[:, :, -1]

    q = np.quantile(mu_d, [0.03, 0.10, 0.5, 0.90, 0.97], axis=0)  # (5, S, T)
    path = pd.DataFrame(
        [
            {"date": days[t], "series": series[s], "mean": mu_d[:, s, t].mean(), "q03": q[0, s, t], "q10": q[1, s, t],
             "median": q[2, s, t], "q90": q[3, s, t], "q97": q[4, s, t]}
            for s in range(S) for t in range(T)
        ]
    )
    n_by_p = wide.groupby("pollster")["poll_id"].nunique()
    house_rows = []
    if P > 1:
        hd = post["house"].stack(sample=("chain", "draw")).transpose("sample", ...).to_numpy()
        for s in range(S):
            for j, pname in enumerate(pollsters):
                v = hd[:, s, j]
                house_rows.append({"series": series[s], "pollster": pname, "mean": v.mean(),
                                   "q03": np.quantile(v, 0.03), "q97": np.quantile(v, 0.97),
                                   "n_polls": int(n_by_p.get(pname, 0))})
    names = ["sigma_rw", "sigma_ns"] + (["sigma_house"] if P > 1 else []) + (["rho"] if two_regime else [])
    prm = []
    for nm in names:
        v = post[nm].stack(sample=("chain", "draw")).transpose("sample", ...).to_numpy()
        for s in range(S):
            prm.append({"series": series[s], "param": nm, "mean": v[:, s].mean(),
                        "q03": np.quantile(v[:, s], 0.03), "q97": np.quantile(v[:, s], 0.97)})
    vn = names + ["init"]
    diag = {
        "rhat_max": float(az.rhat(idata, var_names=vn).to_array().max()),
        "ess_bulk_min": float(az.ess(idata, var_names=vn, method="bulk").to_array().min()),
        "ess_tail_min": float(az.ess(idata, var_names=vn, method="tail").to_array().min()),
        "divergences": int(idata.sample_stats["diverging"].sum()),
        "draws": int(ed.shape[0]),
        "target_accept": float(sk["target_accept"]),
        "tune": int(sk["tune"]),
    }
    diag["converged"] = converged(diag)
    return FitResult(
        series=series, days=days, election_day_draws=ed, path=path, house=pd.DataFrame(house_rows),
        params=pd.DataFrame(prm), diagnostics=diag, n_polls=int(wide["poll_id"].nunique()), cutoff=cutoff,
        variant="two_regime" if two_regime else "single_regime", polls=wide,
    )

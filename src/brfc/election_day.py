"""Round-specific election-day deviation term, estimated leave-one-election-out (LOEO).

Definition. For a past election e and round r, the eve deviation of forecast category c is
    d[e, c] = observed valid share - model's eve posterior-mean share (pp),
where the eve forecast uses only polls with fieldwork ending before election day. Categories get a
*rank role* from the forecast itself (not from the outcome, and not from candidate identity):
round 1 -> "rank1", "rank2", "rest" (other named candidates and Others); round 2 -> "rank1" only
(the other runoff share is its complement).

Model (per round type, fitted separately for every target election on the OTHER elections only):
    variant "F" (primary):  d ~ Normal(mu[role], sigma),  mu[role] ~ Normal(0, tau^2),  sigma ~ HalfNormal(s)
    variant "E" (baseline): d ~ Normal(0, sigma),          sigma ~ HalfNormal(s)
mu is integrated analytically given sigma (conjugate normal); sigma is evaluated on a fine grid, so the
posterior is exact up to grid resolution and fully deterministic given the seed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from brfc import config

PRIOR = {"tau": 3.0, "sigma_scale": 3.0}
SIGMA_GRID = np.linspace(0.05, 15.0, 3000)


def assign_roles(categories: list[str], forecast_mean: dict[str, float], round_: int) -> dict[str, str]:
    """Rank roles from forecast means (information available at forecast time)."""
    named = [c for c in categories if c != config.OTHERS_LABEL]
    order = sorted(named, key=lambda c: -forecast_mean[c])
    if round_ == 2:
        return {order[0]: "rank1"}
    roles = dict.fromkeys(categories, "rest")
    roles[order[0]] = "rank1"
    if len(order) > 1:
        roles[order[1]] = "rank2"
    return roles


def loeo_training_set(devs: pd.DataFrame, target_election: str, round_: int) -> pd.DataFrame:
    """Deviations usable for `target_election`: same round type, every OTHER historical election."""
    return devs[(devs["round"] == round_) & (devs["election"] != target_election)].copy()


@dataclass
class ErrorPosterior:
    variant: str
    roles: list[str]
    sigma_draws: np.ndarray  # (N,)
    mu_draws: dict[str, np.ndarray]  # role -> (N,)
    n_train: int
    train_elections: list[str]

    def summary(self) -> dict:
        out = {
            "variant": self.variant,
            "n_train": self.n_train,
            "train_elections": "+".join(self.train_elections),
            "sigma_mean": float(self.sigma_draws.mean()),
        }
        for r, v in self.mu_draws.items():
            out[f"mu_{r}_mean"] = float(v.mean())
            out[f"mu_{r}_q03"] = float(np.quantile(v, 0.03))
            out[f"mu_{r}_q97"] = float(np.quantile(v, 0.97))
        return out


def fit_error_model(
    train: pd.DataFrame,
    variant: str,
    roles: list[str],
    *,
    n_draws: int = 4000,
    seed: int = 0,
    prior: dict | None = None,
) -> ErrorPosterior:
    pr = {**PRIOR, **(prior or {})}
    tau, s = pr["tau"], pr["sigma_scale"]
    rng = np.random.default_rng(seed)
    sig2 = SIGMA_GRID**2
    logpost = -0.5 * (SIGMA_GRID / s) ** 2  # HalfNormal(s) prior, up to a constant
    groups = {r: train.loc[train["role"] == r, "deviation"].to_numpy(dtype=float) for r in roles}
    for d in groups.values():
        n = len(d)
        if n == 0:
            continue
        if variant == "F":
            v = sig2 + n * tau**2
            logdet = (n - 1) * np.log(sig2) + np.log(v)
            quad = (np.sum(d**2) - tau**2 * np.sum(d) ** 2 / v) / sig2
        else:
            logdet = n * np.log(sig2)
            quad = np.sum(d**2) / sig2
        logpost += -0.5 * (logdet + quad)
    w = np.exp(logpost - logpost.max())
    w /= w.sum()
    sigma = rng.choice(SIGMA_GRID, size=n_draws, p=w)
    mu = {}
    for r, d in groups.items():
        if variant == "E":
            mu[r] = np.zeros(n_draws)
            continue
        prec = len(d) / sigma**2 + 1.0 / tau**2
        mean = (np.sum(d) / sigma**2) / prec
        mu[r] = mean + rng.standard_normal(n_draws) / np.sqrt(prec)
    return ErrorPosterior(
        variant=variant,
        roles=roles,
        sigma_draws=sigma,
        mu_draws=mu,
        n_train=len(train),
        train_elections=sorted(train["election"].unique().tolist()),
    )


def apply(
    latent: np.ndarray,
    categories: list[str],
    roles: dict[str, str],
    post: ErrorPosterior | None,
    round_: int,
    seed: int = 0,
) -> np.ndarray:
    """Forecast draws of valid shares: latent consensus + election-day deviation, projected onto the simplex.

    latent: (N, K) draws of the latent share for each category. Round 2 has K == 1 (first runoff candidate);
    the output then has 2 columns [candidate A, candidate B = 100 - A].
    """
    rng = np.random.default_rng(seed)
    n = latent.shape[0]
    x = latent.copy()
    if post is not None:
        idx = rng.integers(0, len(post.sigma_draws), size=n)
        sig = post.sigma_draws[idx]
        if round_ == 2:
            # the deviation is defined for the forecast rank-1 candidate; A's share moves by +d or -d
            sign = 1.0 if roles.get(categories[0]) == "rank1" else -1.0
            x[:, 0] = x[:, 0] + sign * (post.mu_draws["rank1"][idx] + sig * rng.standard_normal(n))
        else:
            for k, c in enumerate(categories):
                mu = post.mu_draws[roles[c]][idx]
                x[:, k] = x[:, k] + mu + sig * rng.standard_normal(n)
    if round_ == 2:
        a = np.clip(x[:, 0], 0.0, 100.0)
        return np.column_stack([a, 100.0 - a])
    x = np.clip(x, 0.0, None)
    return 100.0 * x / x.sum(axis=1, keepdims=True)

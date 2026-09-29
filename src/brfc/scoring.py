"""Forecast scoring against observed valid-vote shares. Pure functions; no model code imported."""

from __future__ import annotations

import numpy as np

from brfc import config


def _event_prob(count: int, n: int) -> float:
    """Monte Carlo event probability with a +0.5 continuity correction (never exactly 0 or 1)."""
    return (count + 0.5) / (n + 1.0)


def score(draws: np.ndarray | None, point: dict[str, float] | None, categories: list[str], actual: dict[str, float],
          round_: int) -> dict:
    """Score one forecast. Pass `draws` (N, K) for probabilistic forecasts, or `point` for point-only baselines.

    Returns share MAE (pp, over all categories incl. Others), signed/absolute top-two margin error (pp, top two
    = the two named candidates with the highest OBSERVED shares), 80%/94% equal-tailed interval coverage,
    first-place (round 1) / winner (round 2) Brier and log score, and for round 1 the Brier score of
    'the leading candidate exceeds 50% of valid votes' (a first-round win)."""
    y = np.array([actual[c] for c in categories])
    named = [c for c in categories if c != config.OTHERS_LABEL]
    top = sorted(named, key=lambda c: -actual[c])[:2]
    i1, i2 = categories.index(top[0]), categories.index(top[1])
    out: dict = {"n_categories": len(categories)}
    if draws is not None:
        mean = draws.mean(axis=0)
        out["probabilistic"] = True
        for lvl in config.INTERVALS:
            lo = np.quantile(draws, (1 - lvl) / 2, axis=0)
            hi = np.quantile(draws, 1 - (1 - lvl) / 2, axis=0)
            tag = int(round(lvl * 100))
            out[f"coverage_{tag}"] = float(np.mean((y >= lo) & (y <= hi)))
            out[f"width_{tag}_mean"] = float(np.mean(hi - lo))
        pred_margin = float(np.mean(draws[:, i1] - draws[:, i2]))
        ni = [categories.index(c) for c in named]
        first = np.array(named)[np.argmax(draws[:, ni], axis=1)]
        n = draws.shape[0]
        p_first = {c: _event_prob(int(np.sum(first == c)), n) for c in named}
        z = sum(p_first.values())
        p_first = {c: p / z for c, p in p_first.items()}
        winner = top[0]
        out["p_actual_first"] = p_first[winner]
        out["brier_first"] = float(sum((p_first[c] - (c == winner)) ** 2 for c in named))
        out["log_first"] = float(np.log(p_first[winner]))
        if round_ == 1:
            p50 = _event_prob(int(np.sum(draws[:, ni].max(axis=1) > 50.0)), n)
            out["p_first_round_win"] = p50
            out["brier_first_round_win"] = float((p50 - float(actual[winner] > 50.0)) ** 2)
    else:
        mean = np.array([point[c] for c in categories])
        out["probabilistic"] = False
        pred_margin = float(mean[i1] - mean[i2])
    out["mae"] = float(np.mean(np.abs(mean - y)))
    out["margin_pred"] = pred_margin
    out["margin_actual"] = float(y[i1] - y[i2])
    out["margin_error"] = pred_margin - out["margin_actual"]
    out["margin_abs_error"] = abs(out["margin_error"])
    return out


def category_rows(draws: np.ndarray | None, point: dict[str, float] | None, categories: list[str],
                  actual: dict[str, float]) -> list[dict]:
    rows = []
    for k, c in enumerate(categories):
        r = {"category": c, "actual": actual[c]}
        if draws is not None:
            r["mean"] = float(draws[:, k].mean())
            for lvl in config.INTERVALS:
                tag = int(round(lvl * 100))
                r[f"lo{tag}"] = float(np.quantile(draws[:, k], (1 - lvl) / 2))
                r[f"hi{tag}"] = float(np.quantile(draws[:, k], 1 - (1 - lvl) / 2))
                r[f"in{tag}"] = bool(r[f"lo{tag}"] <= actual[c] <= r[f"hi{tag}"])
        else:
            r["mean"] = float(point[c])
        r["error"] = r["mean"] - actual[c]
        rows.append(r)
    return rows

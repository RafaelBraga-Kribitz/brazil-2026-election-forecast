"""Poll-derived point baselines and their calibrated probabilistic conversion.

A point baseline publishes no probabilities. To compare calibration fairly we build a *calibrated
probabilistic conversion of the point baseline*: point estimate + Normal(0, RMSE) per category, where RMSE is
that baseline's own historical error (same round type and horizon) on the OTHER elections only (LOEO),
followed by the same simplex projection as the model. It is labelled as such everywhere; the baseline itself
did not publish these probabilities.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from brfc import config
from brfc.transform import series_table

LOOKBACK_DAYS = 14


def _wide_for(kept_rows: pd.DataFrame, named: list[str]) -> pd.DataFrame:
    return series_table(kept_rows, named)


def latest_per_pollster(kept_rows: pd.DataFrame, named: list[str], cutoff: date, round_: int) -> dict | None:
    """Baseline B: each pollster's most recent poll with fieldwork ending in the 14 days up to `cutoff`,
    averaged with equal weight per pollster (so frequent publishers do not dominate)."""
    w = _wide_for(kept_rows, named)
    fe = pd.to_datetime(w["field_end"]).dt.date
    w = w[(fe > cutoff - timedelta(days=LOOKBACK_DAYS)) & (fe <= cutoff)]
    if w.empty:
        return None
    w = w.sort_values(["field_end", "field_start", "poll_id"]).groupby("pollster").tail(1)
    return _point(w, named, round_) | {"_n_pollsters": int(len(w))}


def final_poll_of(kept_rows: pd.DataFrame, named: list[str], cutoff: date, pollster: str, round_: int) -> dict | None:
    """Baselines C/D: the named pollster's most recent poll available at `cutoff` (within 14 days), else None."""
    w = _wide_for(kept_rows, named)
    fe = pd.to_datetime(w["field_end"]).dt.date
    w = w[(w["pollster"] == pollster) & (fe > cutoff - timedelta(days=LOOKBACK_DAYS)) & (fe <= cutoff)]
    if w.empty:
        return None
    w = w.sort_values(["field_end", "field_start", "poll_id"]).tail(1)
    return _point(w, named, round_) | {"_poll_id": w["poll_id"].iloc[0], "_field_end": str(w["field_end"].iloc[0])}


def _point(w: pd.DataFrame, named: list[str], round_: int) -> dict:
    cols = named + ([config.OTHERS_LABEL] if len(named) > 1 else [])
    vals = {c: float(w[c].mean(skipna=True)) for c in cols}
    if any(np.isnan(v) for v in vals.values()):
        return {c: np.nan for c in cols}
    if round_ == 2:
        a = vals[named[0]]
        return {named[0]: a, "_other": 100.0 - a}
    s = sum(vals.values())
    return {c: 100.0 * v / s for c, v in vals.items()}


def as_categories(point: dict, categories: list[str], round_: int) -> dict[str, float] | None:
    """Map a baseline point dict to the scoring categories (round 2: [A, B])."""
    if point is None:
        return None
    if round_ == 2:
        a = point[categories[0]]
        return None if np.isnan(a) else {categories[0]: a, categories[1]: 100.0 - a}
    vals = {c: point.get(c, np.nan) for c in categories}
    return None if any(np.isnan(v) for v in vals.values()) else vals


def probabilistic(point: dict[str, float], categories: list[str], rmse: float, round_: int, n: int = 4000,
                  seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if round_ == 2:
        a = np.clip(point[categories[0]] + rmse * rng.standard_normal(n), 0.0, 100.0)
        return np.column_stack([a, 100.0 - a])
    x = np.array([point[c] for c in categories])[None, :] + rmse * rng.standard_normal((n, len(categories)))
    x = np.clip(x, 0.0, None)
    return 100.0 * x / x.sum(axis=1, keepdims=True)


def loeo_rmse(errors: pd.DataFrame, baseline: str, target_election: str, round_: int, horizon: int) -> float | None:
    """RMSE of `baseline` across categories of the OTHER elections, same round type and horizon."""
    e = errors[(errors["baseline"] == baseline) & (errors["round"] == round_) & (errors["horizon"] == horizon)
               & (errors["election"] != target_election)]
    if e.empty:
        return None
    return float(np.sqrt(np.mean(e["error"].to_numpy() ** 2)))

"""Probability of being elected president under this model, combining first-round paths with head-to-head fits.

Pure numpy/pandas arithmetic on forecast draws. No model code, no results and no runoff pairing are read here:
the forecast functions receive first-round draws and head-to-head (h2h) win probabilities only.

Definitions.
- A first-round draw is an outright win when its top named candidate's valid share is strictly above 50%
  ("Others" is not a candidate and can never win or enter a pair). Otherwise the draw's runoff pair is the two
  named candidates with the largest shares. Ties in share are broken by category order (deterministic).
- Pair key = the two candidate names in alphabetical order. The h2h series is the first-listed candidate's
  valid share; the second-listed candidate's share is its complement.
- P(X elected) = P(X outright) + sum over pairs of P(pair and no outright) * P(X wins | pair).
  A pair without an h2h fit adds its mass to "unmodelled", which is never reallocated, so
  sum_X P(X elected) + unmodelled == 1.
- First-round draws and h2h draws are treated as independent.
"""

from __future__ import annotations

import math

import numpy as np

from brfc import config

OUTRIGHT_PREFIX = "outright:"
PAIR_PREFIX = "pair:"
PAIR_SEP = " vs "
TOL = 1e-9


def pair_key(a: str, b: str) -> tuple[str, str]:
    """Canonical (alphabetical) key of a runoff pair."""
    if a == b:
        raise ValueError(f"a runoff pair needs two different candidates, got {a!r} twice")
    first, second = sorted((a, b))
    return first, second


def pair_label(pair: tuple[str, str]) -> str:
    """Display label of a pair key, e.g. "A vs B"."""
    return f"{pair[0]}{PAIR_SEP}{pair[1]}"


def _classify(
    draws: np.ndarray, categories: list[str], others_label: str
) -> tuple[list[str], np.ndarray, np.ndarray, np.ndarray]:
    """Per draw: (named, outright flag, index of first named, index of second named) with indices into `named`."""
    x = np.asarray(draws, dtype=float)
    if x.ndim != 2 or x.shape[1] != len(categories):
        raise ValueError(f"draws must be (N, {len(categories)}), got {x.shape}")
    if x.shape[0] == 0:
        raise ValueError("draws are empty")
    if not np.all(np.isfinite(x)):
        raise ValueError("draws contain non-finite values")
    if len(set(categories)) != len(categories):
        raise ValueError("categories must be unique")
    named = [c for c in categories if c != others_label]
    if len(named) < 2:
        raise ValueError("at least two named candidates are needed to form a runoff pair")
    sub = x[:, [categories.index(c) for c in named]]
    order = np.argsort(-sub, axis=1, kind="stable")  # ties -> category order
    first, second = order[:, 0], order[:, 1]
    outright = sub[np.arange(len(sub)), first] > 50.0
    return named, outright, first, second


def first_round_paths(draws: np.ndarray, categories: list[str], others_label: str = config.OTHERS_LABEL) -> np.ndarray:
    """Path label of every first-round draw: "outright:<cand>" or "pair:<a> vs <b>" (alphabetical pair)."""
    named, outright, first, second = _classify(draws, categories, others_label)
    out = []
    for o, i, j in zip(outright, first, second, strict=True):
        if o:
            out.append(f"{OUTRIGHT_PREFIX}{named[i]}")
        else:
            out.append(f"{PAIR_PREFIX}{pair_label(pair_key(named[i], named[j]))}")
    return np.array(out, dtype=str)


def _sorted_by_mass(d: dict) -> dict:
    return dict(sorted(d.items(), key=lambda kv: (-kv[1], kv[0])))


def path_masses(draws: np.ndarray, categories: list[str], others_label: str = config.OTHERS_LABEL) -> dict:
    """Exact Monte Carlo path frequencies (count / N, no smoothing): {"outright": {cand: p}, "pairs": {(a, b): p}}.

    Only paths that occur in at least one draw are listed; all masses together sum to 1."""
    named, outright, first, second = _classify(draws, categories, others_label)
    n = len(outright)
    counts_out: dict[str, int] = {}
    counts_pair: dict[tuple[str, str], int] = {}
    for o, i, j in zip(outright, first, second, strict=True):
        if o:
            counts_out[named[i]] = counts_out.get(named[i], 0) + 1
        else:
            k = pair_key(named[i], named[j])
            counts_pair[k] = counts_pair.get(k, 0) + 1
    return {
        "outright": _sorted_by_mass({c: k / n for c, k in counts_out.items()}),
        "pairs": _sorted_by_mass({p: k / n for p, k in counts_pair.items()}),
    }


def win_given_pair(h2h_draws: np.ndarray, pair: tuple[str, str]) -> dict[str, float]:
    """P(each candidate wins | pair) from h2h draws: share of draws with the candidate above the other (ties 0.5).

    `h2h_draws` is (N, 2) valid shares for (pair[0], pair[1]); an (N,) or (N, 1) array holds pair[0]'s share and
    pair[1]'s is taken as 100 minus it. With shares summing to 100, "above the other" equals "above 50"."""
    a, b = pair
    if a == b:
        raise ValueError(f"a runoff pair needs two different candidates, got {a!r} twice")
    x = np.asarray(h2h_draws, dtype=float)
    if x.ndim == 1:
        x = x[:, None]
    if x.ndim != 2 or x.shape[1] not in (1, 2) or x.shape[0] == 0:
        raise ValueError(f"h2h draws must be (N, 2), (N, 1) or (N,), got {np.shape(h2h_draws)}")
    if not np.all(np.isfinite(x)):
        raise ValueError("h2h draws contain non-finite values")
    first = x[:, 0]
    other = x[:, 1] if x.shape[1] == 2 else 100.0 - first
    p = (np.sum(first > other) + 0.5 * np.sum(first == other)) / len(first)
    return {a: float(p), b: float(1.0 - p)}


def _normalise_h2h(h2h: dict) -> dict[tuple[str, str], dict[str, float]]:
    out: dict[tuple[str, str], dict[str, float]] = {}
    for k, win in (h2h or {}).items():
        key = pair_key(*k)
        if key in out:
            raise ValueError(f"pair {pair_label(key)} given twice")
        if win is None:
            continue
        if set(win) != set(key):
            raise ValueError(f"win probabilities for {pair_label(key)} must name exactly its two candidates")
        vals = {c: float(win[c]) for c in key}
        if any(not (0.0 <= v <= 1.0) for v in vals.values()) or abs(sum(vals.values()) - 1.0) > TOL:
            raise ValueError(f"win probabilities for {pair_label(key)} must lie in [0, 1] and sum to 1")
        out[key] = vals
    return out


def president_probabilities(
    r1_draws: np.ndarray, categories: list[str], h2h: dict, others_label: str = config.OTHERS_LABEL
) -> dict:
    """Combine first-round path masses with P(win | pair).

    `h2h` maps a pair (any order; normalised with `pair_key`) to {candidate: P(wins | pair)}, e.g. the output of
    `win_given_pair`. A pair missing from `h2h` (or mapped to None) is unmodelled.

    Returns {"probabilities": {cand: p} (every named first-round category, zeros included),
             "unmodelled": p, "outright": {cand: p},
             "pairs": [{"pair": "A vs B", "candidates": [A, B], "mass": m, "modelled": bool,
                        "win": {A: p, B: p} or None}]}  (pairs by decreasing mass)."""
    masses = path_masses(r1_draws, categories, others_label)
    wins = _normalise_h2h(h2h)
    named = [c for c in categories if c != others_label]
    prob = dict.fromkeys(named, 0.0)
    for c, m in masses["outright"].items():
        prob[c] += m
    unmodelled = 0.0
    pairs = []
    for key, m in masses["pairs"].items():
        win = wins.get(key)
        if win is None:
            unmodelled += m
        else:
            for c in key:
                prob[c] += m * win[c]
        pairs.append(
            {
                "pair": pair_label(key),
                "candidates": list(key),
                "mass": m,
                "modelled": win is not None,
                "win": dict(win) if win is not None else None,
            }
        )
    total = sum(prob.values()) + unmodelled
    if abs(total - 1.0) > TOL:
        raise RuntimeError(f"probabilities sum to {total!r}, not 1")
    return {
        "probabilities": _sorted_by_mass(prob),
        "unmodelled": unmodelled,
        "outright": dict(masses["outright"]),
        "pairs": pairs,
    }


def score_president(probabilities: dict[str, float], unmodelled: float, winner: str, eps: float = 1e-4) -> dict:
    """Multi-category Brier and log score of the elected candidate.

    Brier categories: every candidate with nonzero probability, the elected candidate (even at probability 0) and
    the "unmodelled" bucket (outcome 0). Candidates at probability 0 other than the winner add 0, so this equals
    the Brier score over all candidates. Log score = log(max(P(winner), eps))."""
    p = {c: float(v) for c, v in probabilities.items()}
    if any(v < 0.0 for v in p.values()) or unmodelled < 0.0:
        raise ValueError("probabilities must be non-negative")
    if abs(sum(p.values()) + unmodelled - 1.0) > 1e-6:
        raise ValueError("probabilities plus unmodelled must sum to 1")
    p_winner = p.get(winner, 0.0)
    cats = {c for c, v in p.items() if v > 0.0} | {winner}
    brier = sum((p.get(c, 0.0) - (c == winner)) ** 2 for c in cats) + float(unmodelled) ** 2
    return {"brier": float(brier), "log": math.log(max(p_winner, eps)), "p_winner": p_winner}

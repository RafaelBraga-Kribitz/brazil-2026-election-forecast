"""Conditional president probabilities: first-round paths x head-to-head win probabilities (synthetic draws only)."""

from __future__ import annotations

import inspect
import json
import math
from pathlib import Path

import numpy as np
import pytest

from brfc import conditional, figures

CATS = ["A", "B", "C", "Others"]
SRC = Path(__file__).resolve().parents[1] / "src" / "brfc" / "conditional.py"


def _hand_draws() -> np.ndarray:
    """10 draws: 2 outright A, 5 pair A-B, 2 pair B-C, 1 pair A-C."""
    rows = [[55, 25, 10, 10]] * 2 + [[40, 35, 15, 10]] * 5 + [[20, 40, 30, 10]] * 2 + [[40, 5, 45, 10]]
    return np.array(rows, dtype=float)


def _random_draws(n=2000, seed=3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = np.clip(np.array([44.0, 38.0, 9.0, 9.0]) + rng.normal(0, 5, size=(n, 4)), 0, None)
    return 100 * x / x.sum(axis=1, keepdims=True)


def test_pair_key_is_alphabetical_and_rejects_identical():
    assert conditional.pair_key("B", "A") == ("A", "B") == conditional.pair_key("A", "B")
    with pytest.raises(ValueError):
        conditional.pair_key("A", "A")


def test_path_masses_sum_to_one():
    m = conditional.path_masses(_random_draws(), CATS)
    assert sum(m["outright"].values()) + sum(m["pairs"].values()) == pytest.approx(1.0, abs=1e-12)
    assert all(a < b for a, b in m["pairs"])


def test_path_masses_match_labels():
    d = _random_draws()
    labels = conditional.first_round_paths(d, CATS)
    m = conditional.path_masses(d, CATS)
    for c, p in m["outright"].items():
        assert np.mean(labels == f"outright:{c}") == p
    for (a, b), p in m["pairs"].items():
        assert np.mean(labels == f"pair:{a} vs {b}") == p


def test_outright_requires_strictly_more_than_50():
    d = np.array([[50.0, 30.0, 20.0, 0.0], [50.01, 29.99, 20.0, 0.0]])
    assert conditional.first_round_paths(d, CATS).tolist() == ["pair:A vs B", "outright:A"]
    m = conditional.path_masses(d, CATS)
    assert m["outright"] == {"A": 0.5} and m["pairs"] == {("A", "B"): 0.5}


def test_others_never_enters_a_pair_nor_wins_outright():
    d = np.array(
        [
            [45.0, 15.0, 10.0, 30.0],  # Others second
            [20.0, 15.0, 10.0, 55.0],  # Others above 50
            [5.0, 30.0, 3.0, 62.0],  # Others first, B top named
        ]
    )
    labels = conditional.first_round_paths(d, CATS).tolist()
    assert labels == ["pair:A vs B", "pair:A vs B", "pair:A vs B"]
    assert not any("Others" in lab for lab in labels)
    r = conditional.president_probabilities(d, CATS, {})
    assert "Others" not in r["probabilities"] and r["outright"] == {}


def test_president_probabilities_hand_computed():
    h2h = {("A", "B"): {"A": 0.6, "B": 0.4}, ("B", "C"): {"B": 0.3, "C": 0.7}}
    r = conditional.president_probabilities(_hand_draws(), CATS, h2h)
    assert r["outright"] == {"A": pytest.approx(0.2)}
    assert r["probabilities"] == {"A": pytest.approx(0.5), "B": pytest.approx(0.26), "C": pytest.approx(0.14)}
    assert r["unmodelled"] == pytest.approx(0.1)
    pairs = {p["pair"]: p for p in r["pairs"]}
    assert [p["pair"] for p in r["pairs"]] == ["A vs B", "B vs C", "A vs C"]  # by decreasing mass
    assert pairs["A vs B"]["mass"] == pytest.approx(0.5) and pairs["A vs B"]["modelled"]
    assert pairs["A vs C"] == {"pair": "A vs C", "candidates": ["A", "C"], "mass": 0.1, "modelled": False, "win": None}
    assert sum(r["probabilities"].values()) + r["unmodelled"] == pytest.approx(1.0, abs=1e-9)


def test_unmodelled_pair_mass_is_not_reallocated():
    d = _hand_draws()
    full = conditional.president_probabilities(
        d, CATS, {("A", "B"): {"A": 0.6, "B": 0.4}, ("B", "C"): {"B": 0.3, "C": 0.7}, ("A", "C"): {"A": 1, "C": 0}}
    )
    part = conditional.president_probabilities(
        d, CATS, {("A", "B"): {"A": 0.6, "B": 0.4}, ("B", "C"): {"B": 0.3, "C": 0.7}}
    )
    assert full["unmodelled"] == 0.0 and part["unmodelled"] == pytest.approx(0.1)
    # dropping the A-C fit removes exactly A's 0.1 via A-C; nobody else gains
    assert part["probabilities"]["A"] == pytest.approx(full["probabilities"]["A"] - 0.1)
    for c in ("B", "C"):
        assert part["probabilities"][c] == pytest.approx(full["probabilities"][c])


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_president_probabilities_plus_unmodelled_equal_one(seed):
    rng = np.random.default_rng(seed)
    d = _random_draws(3000, seed)
    pairs = conditional.path_masses(d, CATS)["pairs"]
    h2h = {}
    for k, key in enumerate(pairs):
        if k % 2 == 0:  # model every other pair
            h2h[key] = conditional.win_given_pair(rng.normal(50, 4, size=(500, 1)), key)
    r = conditional.president_probabilities(d, CATS, h2h)
    assert abs(sum(r["probabilities"].values()) + r["unmodelled"] - 1.0) <= 1e-9
    assert all(0.0 <= p <= 1.0 for p in r["probabilities"].values())


def test_empty_h2h_leaves_everything_not_outright_unmodelled():
    d = _hand_draws()
    r = conditional.president_probabilities(d, CATS, {})
    assert r["probabilities"] == {"A": pytest.approx(0.2), "B": 0.0, "C": 0.0}
    assert r["unmodelled"] == pytest.approx(0.8)
    assert all(not p["modelled"] and p["win"] is None for p in r["pairs"])


def test_win_given_pair_counts_and_ties():
    pair = ("A", "B")
    d = np.array([[60.0, 40.0], [50.0, 50.0], [45.0, 55.0], [70.0, 30.0]])
    assert conditional.win_given_pair(d, pair) == {"A": 0.625, "B": 0.375}
    assert conditional.win_given_pair(d[:, 0], pair) == conditional.win_given_pair(d, pair)  # complement form


def test_single_candidate_at_100_in_every_draw():
    w = conditional.win_given_pair(np.tile([100.0, 0.0], (50, 1)), ("A", "B"))
    assert w == {"A": 1.0, "B": 0.0}
    d = np.array([[40.0, 35.0, 15.0, 10.0]] * 4)
    r = conditional.president_probabilities(d, CATS, {("A", "B"): w})
    assert r["probabilities"] == {"A": 1.0, "B": 0.0, "C": 0.0} and r["unmodelled"] == 0.0
    s = conditional.score_president(r["probabilities"], r["unmodelled"], "A")
    assert s == {"brier": 0.0, "log": 0.0, "p_winner": 1.0}
    # first round: one candidate at 100% in every draw is always an outright win
    r1 = conditional.president_probabilities(np.array([[100.0, 0.0, 0.0, 0.0]] * 3), CATS, {})
    assert r1["outright"] == {"A": 1.0} and r1["pairs"] == [] and r1["unmodelled"] == 0.0


def test_determinism_and_order_invariance():
    d = _random_draws(1500, 9)
    h2h = {("B", "A"): {"A": 0.55, "B": 0.45}}  # key in any order is normalised
    a = conditional.president_probabilities(d, CATS, h2h)
    b = conditional.president_probabilities(d.copy(), CATS, dict(h2h))
    c = conditional.president_probabilities(d[::-1].copy(), CATS, {("A", "B"): {"A": 0.55, "B": 0.45}})
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["probabilities"] == pytest.approx(c["probabilities"], abs=1e-12)
    assert a["unmodelled"] == pytest.approx(c["unmodelled"], abs=1e-12)
    np.testing.assert_array_equal(conditional.first_round_paths(d, CATS), conditional.first_round_paths(d, CATS))


def test_invalid_h2h_is_rejected():
    d = _hand_draws()
    with pytest.raises(ValueError):
        conditional.president_probabilities(d, CATS, {("A", "B"): {"A": 0.6, "B": 0.5}})
    with pytest.raises(ValueError):
        conditional.president_probabilities(d, CATS, {("A", "B"): {"A": 0.6, "C": 0.4}})
    with pytest.raises(ValueError):
        conditional.president_probabilities(d, CATS, {("A", "B"): {"A": 1, "B": 0}, ("B", "A"): {"A": 1, "B": 0}})


def test_score_president_hand_computed():
    probs = {"A": 0.5, "B": 0.26, "C": 0.14}
    s = conditional.score_president(probs, 0.1, "B")
    assert s["brier"] == pytest.approx(0.5**2 + 0.74**2 + 0.14**2 + 0.1**2)
    assert s["log"] == pytest.approx(math.log(0.26)) and s["p_winner"] == 0.26
    # winner at probability 0 still enters the Brier sum; log score floored at eps
    z = conditional.score_president({"A": 0.9, "B": 0.0}, 0.1, "B")
    assert z["brier"] == pytest.approx(0.81 + 1.0 + 0.01)
    assert z["log"] == pytest.approx(math.log(1e-4)) and z["p_winner"] == 0.0
    with pytest.raises(ValueError):
        conditional.score_president({"A": 0.5}, 0.1, "A")


def test_forecast_functions_take_no_outcome_and_read_no_results():
    forbidden = {"actual", "actuals", "result", "results", "outcome", "outcomes", "winner", "pair_actual"}
    for f in (
        conditional.first_round_paths,
        conditional.path_masses,
        conditional.win_given_pair,
        conditional.president_probabilities,
    ):
        assert not set(inspect.signature(f).parameters) & forbidden, f.__name__
    text = SRC.read_text(encoding="utf-8")
    for token in (
        "RUNOFF_PAIRS",
        "load_results",
        "actual_shares",
        "results_secondary",
        "import brfc.model",
        "from brfc import model",
        "brfc.pipeline",
    ):
        assert token not in text, token


def _synthetic_president_json(path: Path, *, modelled=True) -> Path:
    pairs = [
        {
            "pair": "A vs B",
            "mass": 0.5,
            "modelled": modelled,
            "n_polls": 12,
            "win": {"A": 0.6, "B": 0.4} if modelled else None,
            "share_first": {"candidate": "A", "median": 51.2, "q03": 45.0, "q10": 47.5, "q90": 55.0, "q97": 57.3}
            if modelled
            else None,
        },
        {
            "pair": "B vs C",
            "mass": 0.2,
            "modelled": modelled,
            "n_polls": 9,
            "win": {"B": 0.3, "C": 0.7} if modelled else None,
            "share_first": {"candidate": "B", "median": 48.0, "q03": 42.0, "q10": 44.5, "q90": 51.5, "q97": 54.0}
            if modelled
            else None,
        },
        {"pair": "A vs C", "mass": 0.1, "modelled": False, "n_polls": 3, "win": None, "share_first": None},
    ]
    if modelled:
        probs, unmod = {"A": 0.5, "B": 0.26, "C": 0.14}, 0.1
    else:
        probs, unmod = {"A": 0.2, "B": 0.0, "C": 0.0}, 0.8
    doc = {
        "status": "PRELIMINARY",
        "cutoff": "2026-10-03",
        "label": "validated on 3 elections",
        "probabilities": probs,
        "unmodelled": unmod,
        "outright": {"A": 0.2},
        "pairs": pairs,
    }
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


@pytest.mark.parametrize("modelled", [True, False])
def test_president_probability_figure_renders(tmp_path, modelled):
    src = _synthetic_president_json(tmp_path / "president_2026.json", modelled=modelled)
    out = figures.president_probability(out_dir=tmp_path / "fig", json_path=src)
    assert out == tmp_path / "fig" / "president_probability.png"
    data = out.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 10_000


def test_president_figure_rejects_inconsistent_paths(tmp_path):
    src = _synthetic_president_json(tmp_path / "p.json")
    doc = json.loads(src.read_text(encoding="utf-8"))
    doc["probabilities"]["A"] = 0.7
    src.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError):
        figures.president_probability(out_dir=tmp_path, json_path=src)


@pytest.mark.parametrize(
    ("label", "n"),
    [(3, 3), ("3", 3), ("validated on 2 elections only", 2), ("2014+2018+2022", 3), ("LOEO 2018, 2022", 2)],
)
def test_n_validated_elections(label, n):
    assert figures.n_validated_elections(label) == n

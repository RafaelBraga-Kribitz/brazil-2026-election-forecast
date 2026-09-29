"""Tests 7-8: baseline calculations and scoring are reproducible and correct on known values."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from brfc import baselines, scoring, transform
from tests.conftest import poll_rows


def _kept(rows):
    d = transform.to_valid_votes(pd.DataFrame(rows))
    d["round"] = d["round"].astype(int)
    return d


def test_latest_per_pollster_weights_pollsters_equally():
    rows = []
    for k in range(5):  # a frequent publisher
        rows += poll_rows(
            "2022",
            1,
            "Freq",
            f"2022-09-{20 + k:02d}",
            f"2022-09-{20 + k:02d}",
            1000,
            {"Lula": 50, "Jair Bolsonaro": 40, "Ciro Gomes": 10},
        )
    rows += poll_rows(
        "2022", 1, "Rare", "2022-09-25", "2022-09-25", 1000, {"Lula": 40, "Jair Bolsonaro": 50, "Ciro Gomes": 10}
    )
    p = baselines.latest_per_pollster(_kept(rows), ["Lula", "Jair Bolsonaro", "Ciro Gomes"], date(2022, 9, 30), 1)
    assert p["_n_pollsters"] == 2
    assert p["Lula"] == pytest.approx(45.0) and p["Jair Bolsonaro"] == pytest.approx(45.0)


def test_final_poll_respects_cutoff():
    rows = poll_rows("2022", 1, "Datafolha", "2022-09-20", "2022-09-21", 1000, {"Lula": 50, "Jair Bolsonaro": 40})
    rows += poll_rows("2022", 1, "Datafolha", "2022-09-28", "2022-09-29", 1000, {"Lula": 48, "Jair Bolsonaro": 42})
    p = baselines.final_poll_of(_kept(rows), ["Lula", "Jair Bolsonaro"], date(2022, 9, 25), "Datafolha", 1)
    assert p["_field_end"] == "2022-09-21"


def test_probabilistic_conversion_reproducible():
    pt = {"A": 45.0, "B": 40.0, "Others": 15.0}
    a = baselines.probabilistic(pt, ["A", "B", "Others"], 2.0, 1, seed=5)
    b = baselines.probabilistic(pt, ["A", "B", "Others"], 2.0, 1, seed=5)
    np.testing.assert_array_equal(a, b)
    np.testing.assert_allclose(a.sum(axis=1), 100.0)


def test_point_scores_known_values():
    cats = ["A", "B", "Others"]
    s = scoring.score(None, {"A": 50.0, "B": 30.0, "Others": 20.0}, cats, {"A": 48.0, "B": 36.0, "Others": 16.0}, 1)
    assert s["mae"] == pytest.approx(4.0)
    assert s["margin_pred"] == pytest.approx(20.0) and s["margin_actual"] == pytest.approx(12.0)
    assert s["margin_error"] == pytest.approx(8.0)


def test_probabilistic_scores_known_values():
    cats = ["A", "B"]
    draws = np.column_stack([np.full(999, 60.0), np.full(999, 40.0)])
    s = scoring.score(draws, None, cats, {"A": 55.0, "B": 45.0}, 2)
    p = (999 + 0.5) / 1000.0
    q = (0 + 0.5) / 1000.0
    p, q = p / (p + q), q / (p + q)
    assert s["p_actual_first"] == pytest.approx(p)
    assert s["brier_first"] == pytest.approx((p - 1) ** 2 + q**2)
    assert s["coverage_94"] == 0.0 and s["mae"] == pytest.approx(5.0)
    again = scoring.score(draws, None, cats, {"A": 55.0, "B": 45.0}, 2)
    assert again == s

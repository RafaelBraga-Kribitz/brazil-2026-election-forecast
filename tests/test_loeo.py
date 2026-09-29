"""Test 6: leave-one-election-out isolation for the election-day term and baseline error distributions."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from brfc import baselines, election_day


@pytest.fixture
def devs():
    rows = []
    for e, shift in (("2014", 1.0), ("2018", 2.0), ("2022", 3.0)):
        for role in ("rank1", "rank2", "rest"):
            rows.append({"election": e, "round": 1, "role": role, "deviation": shift})
        rows.append({"election": e, "round": 2, "role": "rank1", "deviation": -shift})
    return pd.DataFrame(rows)


@pytest.mark.parametrize("target,expected", [("2014", {"2018", "2022"}), ("2018", {"2014", "2022"}),
                                             ("2022", {"2014", "2018"}), ("2026", {"2014", "2018", "2022"})])
def test_training_set_excludes_target(devs, target, expected):
    for r in (1, 2):
        t = election_day.loeo_training_set(devs, target, r)
        assert set(t["election"]) == expected
        assert (t["round"] == r).all()


def test_target_election_values_cannot_move_its_own_error_model(devs):
    a = election_day.fit_error_model(election_day.loeo_training_set(devs, "2022", 1), "F", ["rank1", "rank2", "rest"])
    poisoned = devs.copy()
    poisoned.loc[poisoned["election"] == "2022", "deviation"] = 99.0
    b = election_day.fit_error_model(election_day.loeo_training_set(poisoned, "2022", 1), "F",
                                     ["rank1", "rank2", "rest"])
    np.testing.assert_array_equal(a.mu_draws["rank2"], b.mu_draws["rank2"])
    assert a.train_elections == ["2014", "2018"]


def test_baseline_rmse_is_loeo():
    err = pd.DataFrame({"baseline": "B", "election": ["2014", "2018", "2022"], "round": 1, "horizon": 1,
                        "category": "x", "error": [1.0, 2.0, 50.0]})
    assert baselines.loeo_rmse(err, "B", "2022", 1, 1) == pytest.approx(np.sqrt((1 + 4) / 2))
    assert baselines.loeo_rmse(err, "B", "2026", 1, 1) == pytest.approx(np.sqrt((1 + 4 + 2500) / 3))


def test_error_model_variant_e_has_zero_mean(devs):
    post = election_day.fit_error_model(election_day.loeo_training_set(devs, "2026", 1), "E", ["rank1", "rank2", "rest"])
    assert all(np.all(v == 0) for v in post.mu_draws.values())

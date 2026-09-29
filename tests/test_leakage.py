"""Tests 1-2 of the validity suite: no outcome can enter a forecast; no future poll can enter a forecast."""

from __future__ import annotations

import inspect
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brfc import baselines, election_day, model, pipeline, transform
from brfc.pipeline import information_set

FORBIDDEN = {"actual", "actuals", "result", "results", "outcome", "outcomes", "m_star", "m_star_pp", "observed_result"}
SRC = Path(__file__).resolve().parents[1] / "src" / "brfc"


def _public_functions(mod):
    return [f for _, f in inspect.getmembers(mod, inspect.isfunction) if f.__module__ == mod.__name__]


@pytest.mark.parametrize("mod", [model, transform, baselines])
def test_forecast_functions_accept_no_outcome_argument(mod):
    for f in _public_functions(mod):
        params = set(inspect.signature(f).parameters)
        assert not params & FORBIDDEN, f"{mod.__name__}.{f.__name__} accepts {params & FORBIDDEN}"


def test_fit_path_functions_accept_no_outcome_argument():
    for f in (pipeline.fit_one, pipeline.information_set, election_day.apply, election_day.fit_error_model):
        assert not set(inspect.signature(f).parameters) & FORBIDDEN, f.__name__


@pytest.mark.parametrize("fname", ["model.py", "transform.py", "names.py", "schema.py", "baselines.py"])
def test_forecast_modules_never_reference_results(fname):
    text = (SRC / fname).read_text(encoding="utf-8")
    for token in ("load_results", "results_secondary", "actual_shares", "results_2026"):
        assert token not in text, f"{fname} references {token}"


def test_stage1_fit_path_never_calls_result_loader(monkeypatch, fixture_2022_r1):
    import brfc.data as data

    def boom(*a, **k):
        raise AssertionError("results loaded in a forecast code path")

    monkeypatch.setattr(data, "load_results", boom)
    monkeypatch.setattr(data, "actual_shares", boom)
    wide, named, dropped, kept, cutoff = information_set(fixture_2022_r1, "2022", 1, 7)
    assert len(wide) > 0


@pytest.mark.parametrize("h", [30, 14, 7, 1])
def test_information_set_contains_no_future_poll(fixture_2022_r1, h):
    wide, named, dropped, kept, cutoff = information_set(fixture_2022_r1, "2022", 1, h)
    assert pd.to_datetime(wide["field_end"]).dt.date.max() <= cutoff
    assert pd.to_datetime(kept["field_end"]).dt.date.max() <= cutoff


def test_publication_date_after_cutoff_excludes_poll(fixture_2022_r1):
    d = fixture_2022_r1.copy()
    cutoff = date(2022, 9, 18)
    last = d[pd.to_datetime(d["field_end"]).dt.date <= cutoff]["poll_id"].iloc[-1]
    d.loc[d["poll_id"] == last, "publication_date"] = str(cutoff + timedelta(days=2))
    kept = transform.available_at(d, cutoff)
    assert last not in set(kept["poll_id"])


def test_future_polls_cannot_change_information_set(fixture_2022_r1):
    """Perturb, add and overlap polls after the cutoff: poll ids, values, grouping and dropped set are unchanged."""
    h = 14
    base = information_set(fixture_2022_r1, "2022", 1, h)
    cutoff = base[4]
    future = pd.to_datetime(fixture_2022_r1["field_end"]).dt.date > cutoff
    perturbed = fixture_2022_r1.copy()
    perturbed.loc[future, "share_reported"] = perturbed.loc[future, "share_reported"] * 3 + 11
    extra = fixture_2022_r1[~future].copy().head(6)
    extra["poll_id"] = extra["poll_id"] + "-future"
    extra["field_start"] = str(cutoff)  # a later wave that overlaps earlier waves of the same pollster
    extra["field_end"] = str(cutoff + timedelta(days=1))
    perturbed = pd.concat([perturbed, extra], ignore_index=True)
    alt = information_set(perturbed, "2022", 1, h)
    pd.testing.assert_frame_equal(base[0].reset_index(drop=True), alt[0].reset_index(drop=True))
    assert base[1] == alt[1]
    assert set(base[2]["poll_id"]) == set(alt[2]["poll_id"])


def test_model_fit_rejects_poll_after_cutoff():
    wide = pd.DataFrame({"poll_id": ["a"], "pollster": ["P"], "field_start": ["2022-09-01"],
                         "field_end": ["2022-09-30"], "sample_size": [1000], "field_mid": [date(2022, 9, 29)],
                         "A": [40.0]})
    with pytest.raises(ValueError, match="information leak"):
        model.fit(wide, ["A"], window_start=date(2022, 8, 3), election_day=date(2022, 10, 2), cutoff=date(2022, 9, 20))


def test_election_day_application_is_deterministic():
    rng = np.random.default_rng(0)
    latent = rng.normal([45, 35, 20], 1, size=(500, 3))
    train = pd.DataFrame({"election": ["2014", "2018"] * 3, "round": 1,
                          "role": ["rank1", "rank1", "rank2", "rank2", "rest", "rest"],
                          "deviation": [1.0, -1.0, 5.0, 4.0, -2.0, -3.0]})
    post = election_day.fit_error_model(train, "F", ["rank1", "rank2", "rest"], seed=1)
    roles = {"A": "rank1", "B": "rank2", "Others": "rest"}
    a = election_day.apply(latent, ["A", "B", "Others"], roles, post, 1, seed=3)
    b = election_day.apply(latent, ["A", "B", "Others"], roles, post, 1, seed=3)
    np.testing.assert_array_equal(a, b)

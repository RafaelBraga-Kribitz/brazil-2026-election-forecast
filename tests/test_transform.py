"""Tests 4-5: poll transformations are deterministic; candidate shares stay valid."""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from brfc import election_day, transform
from brfc.schema import OTHERS
from tests.conftest import poll_rows


def _digest(df: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(df.reset_index(drop=True), index=False).values.tobytes()).hexdigest()


def test_prepare_is_deterministic(fixture_2022_r1):
    a = transform.prepare(fixture_2022_r1, "2022", 1)
    b = transform.prepare(fixture_2022_r1.sample(frac=1.0, random_state=7), "2022", 1)
    cols = ["poll_id", "scenario", "candidate", "valid_vote_share"]
    a = a[cols].sort_values(cols[:3]).reset_index(drop=True)
    b = b[cols].sort_values(cols[:3]).reset_index(drop=True)
    assert _digest(a) == _digest(b)


def test_valid_vote_conversion_sums_to_100_and_preserves_reported():
    d = pd.DataFrame(poll_rows("2022", 1, "P", "2022-09-01", "2022-09-02", 2000,
                               {"Lula": 45, "Jair Bolsonaro": 33, "Ciro Gomes": 7, OTHERS: 3}, blank_null=12))
    v = transform.to_valid_votes(d)
    assert abs(v["valid_vote_share"].sum() - 100) < 1e-9
    assert (v["share_reported"] == d["share_reported"]).all()
    assert abs(v.loc[v["candidate"] == "Lula", "valid_vote_share"].iloc[0] - 100 * 45 / 88) < 1e-9


def test_scenario_rule_prefers_ballot_and_drops_material_off_ballot():
    rows = []
    rows += poll_rows("2018", 1, "P", "2018-09-01", "2018-09-02", 2000,
                      {"Lula": 39, "Jair Bolsonaro": 22, "Ciro Gomes": 8}, scenario="with Lula")
    rows += poll_rows("2018", 1, "P", "2018-09-01", "2018-09-02", 2000,
                      {"Fernando Haddad": 6, "Jair Bolsonaro": 22, "Ciro Gomes": 12}, scenario="with Haddad")
    d = pd.DataFrame(rows)
    out = transform.select_scenarios(d, "2018", 1)
    assert set(out["scenario"]) == {"with Haddad"}


def test_scenario_rule_prefers_fewest_off_ballot_names():
    rows = []
    rows += poll_rows("2026", 1, "P", "2026-09-01", "2026-09-02", 2000,
                      {"Lula": 40, "Flávio Bolsonaro": 36, "Pablo Marçal": 2}, scenario="full")
    rows += poll_rows("2026", 1, "P", "2026-09-01", "2026-09-02", 2000,
                      {"Lula": 41, "Flávio Bolsonaro": 37}, scenario="without Pablo Marçal")
    out = transform.select_scenarios(pd.DataFrame(rows), "2026", 1)
    assert set(out["scenario"]) == {"without Pablo Marçal"}


def test_overlapping_waves_keep_latest():
    rows = []
    rows += poll_rows("2022", 1, "T", "2022-09-20", "2022-09-24", 1500, {"Lula": 45})
    rows += poll_rows("2022", 1, "T", "2022-09-22", "2022-09-26", 1500, {"Lula": 46})
    rows += poll_rows("2022", 1, "T", "2022-09-10", "2022-09-12", 1500, {"Lula": 44})
    kept, dropped = transform.drop_overlapping_waves(pd.DataFrame(rows))
    assert set(kept["field_end"]) == {"2022-09-26", "2022-09-12"}
    assert list(dropped["field_end"]) == ["2022-09-24"]


def test_simplex_projection_valid_shares():
    rng = np.random.default_rng(1)
    latent = rng.normal([45, 35, 3, 17], [2, 2, 3, 2], size=(2000, 4))
    out = election_day.apply(latent, ["A", "B", "C", "Others"], {}, None, 1)
    assert (out >= 0).all()
    np.testing.assert_allclose(out.sum(axis=1), 100.0)
    r2 = election_day.apply(rng.normal(52, 2, size=(1000, 1)), ["A"], {}, None, 2)
    np.testing.assert_allclose(r2.sum(axis=1), 100.0)
    assert ((r2 >= 0) & (r2 <= 100)).all()

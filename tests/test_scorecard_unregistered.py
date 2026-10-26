"""The UNREGISTERED 2026 scorecard: result checks and the share scores computed from stored quantiles."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("score_unreg", ROOT / "scripts" / "score_2026_unregistered.py")
su = importlib.util.module_from_spec(spec)
spec.loader.exec_module(su)

HEADER = (
    "election,round,election_date,candidate,votes,valid_vote_share_pct,total_valid_votes,blank_votes,null_votes,"
    "source_url,source_revision,retrieved_utc,notes\n"
)


def _write(tmp_path, rows):
    p = tmp_path / "r.csv"
    p.write_text(HEADER + "".join(rows), encoding="utf-8")
    return p


def test_committed_results_pass_the_sum_check():
    r1, r2 = su.load_round(su.RESULTS, 1), su.load_round(su.RESULTS, 2)
    assert sum(r1["votes"].values()) == r1["total_valid_votes"]
    assert len(r2["votes"]) == 2
    assert abs(sum(r2["shares"].values()) - 100.0) < 1e-9


def test_votes_not_summing_to_total_are_refused(tmp_path):
    p = _write(tmp_path, ["2026,1,2026-10-04,A,60,60,100,0,0,u,,t,\n", "2026,1,2026-10-04,B,39,39,100,0,0,u,,t,\n"])
    with pytest.raises(ValueError, match="sum"):
        su.load_round(p, 1)


def test_runoff_needs_two_candidates(tmp_path):
    p = _write(tmp_path, ["2026,2,2026-10-25,A,100,100,100,,,u,,t,\n"])
    with pytest.raises(ValueError, match="two candidates"):
        su.load_round(p, 2)


def test_score_model_on_a_toy_forecast():
    cats = [
        {"category": "A", "mean": 50, "q03": 40, "q10": 45, "q90": 55, "q97": 60, "p_first": 0.8, "p_above_50": 0.4},
        {"category": "B", "mean": 45, "q03": 35, "q10": 40, "q90": 50, "q97": 55, "p_first": 0.2, "p_above_50": 0.1},
        {"category": "Others", "mean": 5, "q03": 0, "q10": 1, "q90": 9, "q97": 12, "p_first": None, "p_above_50": None},
    ]
    actual = {"A": 46.0, "B": 49.0, "Others": 5.0}
    s, rows = su.score_model(cats, actual)
    assert s["mae"] == pytest.approx((4 + 4 + 0) / 3)
    assert s["p_actual_first"] == pytest.approx(0.2)
    assert s["brier_first"] == pytest.approx(0.8**2 + 0.8**2)
    assert s["margin_error"] == pytest.approx((45 - 50) - (49 - 46))
    assert s["coverage_80"] == pytest.approx(1.0)
    assert s["brier_first_round_win"] == pytest.approx(0.5**2)
    assert [r["category"] for r in rows] == ["A", "B", "Others"]

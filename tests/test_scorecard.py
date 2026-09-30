"""2026 scorecard (PREREG_ADDENDUM_06). Synthetic results only: no 2026 result is typed before the TSE count."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brfc import config, freeze, scoring
from brfc import scorecard as sc
from brfc.conditional import score_president
from brfc.provenance import sha256_file

ROOT = Path(__file__).resolve().parents[1]
BALLOT = config.BALLOTS["2026"]
NAMED = ["Lula", "Flávio Bolsonaro", "Augusto Cury", "Renan Santos", "Ronaldo Caiado"]
CATS = [*NAMED, config.OTHERS_LABEL]
# synthetic valid-vote counts (not a 2026 result): 100000 valid votes
VOTES = {
    "Lula": 44000,
    "Flávio Bolsonaro": 40000,
    "Ronaldo Caiado": 5000,
    "Romeu Zema": 3000,
    "Renan Santos": 3500,
    "Augusto Cury": 2500,
    "Samara Martins": 600,
    "Rui Costa Pimenta": 300,
    "Wilson Grassi": 300,
    "Hertz Dias": 300,
    "Edmilson Costa": 200,
    "Clariana Barão": 150,
    "Leonardo Avalanche": 150,
}


def _script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _results_csv(path: Path, votes_r1=VOTES, votes_r2=None, total_r1=None) -> Path:
    rows = []
    for round_, votes, total in ((1, votes_r1, total_r1), (2, votes_r2, None)):
        if not votes:
            continue
        t = sum(votes.values()) if total is None else total
        rows += [
            {
                "election": "2026",
                "round": round_,
                "election_date": "2026-10-04" if round_ == 1 else "2026-10-25",
                "candidate": c,
                "votes": v,
                "valid_vote_share_pct": round(100 * v / t, 2),
                "total_valid_votes": t,
                "blank_votes": 1000,
                "null_votes": 2000,
                "source_url": "https://example.org/synthetic",
                "source_revision": "",
                "retrieved_utc": "2026-10-05T00:00:00Z",
                "notes": "synthetic test data",
            }
            for c, v in votes.items()
        ]
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _draws(means, sd=1.5, n=4000, seed=0):
    rng = np.random.default_rng(seed)
    d = np.asarray(means, dtype=float) + rng.normal(0, sd, size=(n, len(means)))
    return 100 * np.clip(d, 0.01, None) / np.clip(d, 0.01, None).sum(axis=1, keepdims=True)


def _write_hashes(fz: Path) -> None:
    files = sorted(p for p in fz.rglob("*") if p.is_file() and p.name != freeze.HASH_FILE)
    (fz / freeze.HASH_FILE).write_text(
        "".join(f"{sha256_file(p)}  {p.relative_to(fz).as_posix()}\n" for p in files), encoding="utf-8"
    )


def _package(fz: Path, *, pollingdata=True, polymarket=True, status="FINAL") -> Path:
    fz.mkdir(parents=True)
    means = [45.0, 41.0, 3.0, 4.0, 5.0, 2.0]
    draws = {m: _draws(means, seed=k) for k, m in enumerate(("F", "E", "E0", "B", "C"))}
    freeze.save_draws(fz / freeze.DRAWS_FILE, draws, CATS)
    doc = {"status": status, "information_cutoff_date": "2026-10-03", "primary_model": "F"}
    (fz / "forecast.json").write_text(json.dumps(doc), encoding="utf-8")
    snap = []
    for b, pt in (("B", [43.0, 42.0, 3.0, 4.0, 5.0, 3.0]), ("C", [46.0, 40.0, 2.0, 4.0, 5.0, 3.0])):
        snap.append({"baseline": b, "status": "ok"} | {f"point_{c}": v for c, v in zip(CATS, pt, strict=True)})
    snap.append({"baseline": "D", "status": "N/A: no qualifying poll in the 14 days to cutoff"})
    if pollingdata:
        for c, v in (("Lula", 44.5), ("Flávio Bolsonaro", 40.5), ("Ronaldo Caiado", 5.0), ("Augusto Cury", 2.0)):
            snap.append({"benchmark": "PollingData", "event_kind": "share", "candidate": c, "valid_share_pct": v})
        snap.append({"benchmark": "PollingData", "event_kind": "share", "candidate": "", "valid_share_pct": 3.0})
        for event, cand, v in (("first_place", "Lula", 84.0), ("runoff_held", "", 100.0)):
            snap.append(
                {
                    "benchmark": "PollingData",
                    "event_kind": "pollingdata_win_probability",
                    "probability_event": event,
                    "candidate": cand,
                    "value_displayed_pct": v,
                }
            )
    if polymarket:
        pm = {"benchmark": "Polymarket", "active": True, "volume_usd": 1000.0}
        for c, p in (("Lula", 0.62), ("Flávio Bolsonaro", 0.40), ("", 0.03)):
            snap.append(pm | {"event_kind": "first_place", "candidate": c, "yes_price": p})
        for label in "ABC":  # placeholder markets ("Candidate A"...): inactive and never traded
            snap.append(pm | {"event_kind": "first_place", "candidate": "", "yes_price": 0.5, "active": False})
            snap[-1]["outcome_label"] = f"Candidate {label}"
            snap[-1]["volume_usd"] = 0.0
        snap.append(pm | {"event_kind": "first_round_outright_win", "candidate": "", "yes_price": 0.1})
        for c, p in (("Lula", 0.52), ("Flávio Bolsonaro", 0.50)):
            snap.append(pm | {"event_kind": "election_winner", "candidate": c, "yes_price": p})
    pd.DataFrame(snap).to_csv(fz / "baseline_snapshot.csv", index=False)
    (fz / "MODEL_VERSION.txt").write_text("synthetic\n", encoding="utf-8")
    alts = {
        "F_E": {"r1_model": "F", "h2h_model": "E", "primary": True},
        "F_F": {"r1_model": "F", "h2h_model": "F", "primary": False},
    }
    for k, v in alts.items():
        v |= {"probabilities": {"Lula": 0.55, "Flávio Bolsonaro": 0.44, "Augusto Cury": 0.0}, "unmodelled": 0.01}
        v["probabilities"]["Lula"] -= 0.01 * (k == "F_F")
        v["unmodelled"] += 0.01 * (k == "F_F")
    president = {
        "status": status,
        "information_cutoff_date": "2026-10-03",
        "probabilities": alts["F_E"]["probabilities"],
        "alternatives": alts,
    }
    (fz / "president.json").write_text(json.dumps(president), encoding="utf-8")
    _write_hashes(fz)
    return fz


# ---------------------------------------------------------------- draws file and package integrity
def test_draws_round_trip_is_exact(tmp_path):
    d = {"F": _draws([50, 30, 20]), "B": _draws([40, 40, 20], seed=3)}
    freeze.save_draws(tmp_path / "d.npz", d, ["a", "b", "Others"])
    cats, back = freeze.load_draws(tmp_path / "d.npz")
    assert cats == ["a", "b", "Others"] and set(back) == {"F", "B"}
    assert all(np.array_equal(back[m], d[m]) and back[m].dtype == np.float64 for m in d)


def test_draws_shape_must_match_categories(tmp_path):
    with pytest.raises(ValueError, match="do not match"):
        freeze.save_draws(tmp_path / "d.npz", {"F": np.zeros((10, 2))}, ["a", "b", "c"])


def test_verify_package_detects_changed_missing_and_unhashed_files(tmp_path):
    fz = _package(tmp_path / "freeze")
    assert freeze.verify_package(fz) == []
    (fz / "extra.txt").write_text("x", encoding="utf-8")
    assert freeze.verify_package(fz) == ["extra.txt: in the package but not hashed"]
    (fz / "extra.txt").unlink()
    (fz / "MODEL_VERSION.txt").write_text("edited\n", encoding="utf-8")
    assert freeze.verify_package(fz) == ["MODEL_VERSION.txt: changed since it was hashed"]
    (fz / "MODEL_VERSION.txt").unlink()
    assert freeze.verify_package(fz) == ["MODEL_VERSION.txt: listed but missing"]
    (fz / freeze.HASH_FILE).unlink()
    assert "missing" in freeze.verify_package(fz)[0]


# ---------------------------------------------------------------- entered result
def test_results_load_and_shares(tmp_path):
    r = sc.load_results_2026(_results_csv(tmp_path / "r.csv"), 1)
    assert r["total_valid_votes"] == 100000 and len(r["votes"]) == len(BALLOT)
    shares = sc.valid_shares(r)
    assert shares["Lula"] == pytest.approx(44.0) and sum(shares.values()) == pytest.approx(100.0)
    act = sc.actual_categories(shares, CATS)
    assert list(act) == CATS
    assert act[config.OTHERS_LABEL] == pytest.approx(100 - 44 - 40 - 2.5 - 3.5 - 5.0)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda v: {k: x for k, x in v.items() if k != "Hertz Dias"}, "without a row"),
        (lambda v: v | {"Pablo Marçal": 10}, "not on the 2026 ballot"),
        (lambda v: v | {"Lula": 44000.5}, "non-negative integers"),
    ],
)
def test_results_checks(tmp_path, change, message):
    votes = change(dict(VOTES))
    with pytest.raises(ValueError, match=message):
        sc.load_results_2026(_results_csv(tmp_path / "r.csv", votes, total_r1=100000), 1)


def test_results_sum_must_equal_valid_total(tmp_path):
    with pytest.raises(ValueError, match="sum to 100000"):
        sc.load_results_2026(_results_csv(tmp_path / "r.csv", total_r1=100001), 1)


def test_results_duplicate_and_runoff_size(tmp_path):
    p = _results_csv(tmp_path / "r.csv", votes_r2={"Lula": 5, "Flávio Bolsonaro": 4})
    df = pd.read_csv(p)
    pd.concat([df, df.iloc[[0]]]).to_csv(p, index=False)
    with pytest.raises(ValueError, match="duplicate"):
        sc.load_results_2026(p, 1)
    p2 = _results_csv(tmp_path / "r2.csv", votes_r2={"Lula": 5, "Flávio Bolsonaro": 4, "Romeu Zema": 1})
    with pytest.raises(ValueError, match="two candidates"):
        sc.load_results_2026(p2, 2)


def test_forecast_scripts_never_read_the_2026_result():
    """No forecast or freeze code imports the scorecard or names the 2026 result file."""
    imports_scorecard = re.compile(r"^\s*(from|import)\s.*scorecard", re.MULTILINE)
    files = [
        ROOT / "scripts" / f
        for f in (
            "forecast_2026.py",
            "forecast_2026_president.py",
            "forecast_2026_runoff.py",
            "refresh_2026_polls.py",
            "snapshot_benchmarks.py",
        )
    ]
    files += [
        ROOT / "src" / "brfc" / f
        for f in ("freeze.py", "forecast.py", "conditional.py", "runoff.py", "pipeline.py", "data.py")
    ]
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "results_2026" not in text, f"{path.name} names the 2026 result file"
        assert not imports_scorecard.search(text), f"{path.name} imports the scorecard"


# ---------------------------------------------------------------- share scores
def test_models_use_the_backtest_scoring_function_and_baselines_the_point_rule(tmp_path):
    fz = _package(tmp_path / "freeze")
    cats, draws = freeze.load_draws(fz / freeze.DRAWS_FILE)
    snap = pd.read_csv(fz / "baseline_snapshot.csv")
    act = sc.actual_categories(sc.valid_shares(sc.load_results_2026(_results_csv(tmp_path / "r.csv"), 1)), cats)
    points = sc.baseline_points(snap, cats)
    assert set(points) == {"B", "C"}
    scores, cat_rows = sc.score_share_models(draws, points, cats, act, 1, baselines=("B", "C", "D"))
    s = scores.set_index("model")
    ref = scoring.score(draws["F"], None, cats, act, 1)
    assert s.loc["F", "mae"] == pytest.approx(ref["mae"]) and s.loc["F", "brier_first"] == pytest.approx(
        ref["brier_first"]
    )
    b_draws = scoring.score(draws["B"], None, cats, act, 1)
    b_point = scoring.score(None, points["B"], cats, act, 1)
    assert s.loc["B", "mae"] == pytest.approx(b_point["mae"])
    assert s.loc["B", "margin_error"] == pytest.approx(b_point["margin_error"])
    assert s.loc["B", "coverage_94"] == pytest.approx(b_draws["coverage_94"])
    assert s.loc["B", "brier_first"] == pytest.approx(b_draws["brier_first"])
    assert s.loc["D", "status"].startswith("N/A")
    b_cat = cat_rows[(cat_rows["model"] == "B") & (cat_rows["category"] == "Lula")].iloc[0]
    assert b_cat["mean"] == 43.0 and b_cat["error"] == pytest.approx(43.0 - 44.0)


def test_pollingdata_subset_rule():
    snap = pd.DataFrame(
        [
            {"benchmark": "PollingData", "event_kind": "share", "candidate": c, "valid_share_pct": v}
            for c, v in (("Lula", 44.0), ("Flávio Bolsonaro", 40.0), ("", 16.0))
        ]
    )
    point, note = sc.pollingdata_point(snap, CATS)
    assert point == {"Lula": 44.0, "Flávio Bolsonaro": 40.0} and "Others not scored" in note
    full = pd.DataFrame(
        [{"benchmark": "PollingData", "event_kind": "share", "candidate": c, "valid_share_pct": 1.0} for c in BALLOT]
    )
    point, _ = sc.pollingdata_point(full, CATS)
    assert point[config.OTHERS_LABEL] == pytest.approx(len(BALLOT) - len(NAMED))
    assert sc.pollingdata_point(pd.DataFrame([{"baseline": "B"}]), CATS)[0] is None


def test_l1_compares_f_with_a_on_the_same_categories():
    draws_f = np.tile([45.0, 40.0, 3.0, 3.0, 5.0, 4.0], (10, 1))
    act = dict(zip(CATS, [44.0, 40.0, 2.5, 3.5, 5.0, 5.0], strict=True))
    a = sc.score_pollingdata({"Lula": 44.0, "Flávio Bolsonaro": 41.0}, draws_f, CATS, act)
    assert a["mae_subset"] == pytest.approx(0.5) and a["F_mae_subset"] == pytest.approx(0.5)
    assert a["margin_error"] == pytest.approx((44 - 41) - (44 - 40)) and "mae" not in a
    scores = pd.DataFrame(
        [
            {"model": "F", "mae": 0.8, "margin_abs_error": 1.0},
            {"model": "B", "mae": 1.0, "margin_abs_error": 0.5},
        ]
    )
    c = sc.criteria_r1(scores, a)
    assert c["L1"]["met"] is True and c["L1"]["met_vs_A"] is True and c["L2"]["met"] is False
    c = sc.criteria_r1(scores, {"model": "A", "status": "N/A"})
    assert c["L1"]["met"] is True and c["L1"]["assessed_against"].startswith("B only")


# ---------------------------------------------------------------- benchmarks
def test_market_prices_are_normalised_with_an_other_bucket():
    live = {"benchmark": "Polymarket", "event_kind": "first_place", "active": True, "volume_usd": 10.0}
    snap = pd.DataFrame(
        [
            live | {"candidate": "Lula", "yes_price": 0.6},
            live | {"candidate": "Flávio Bolsonaro", "yes_price": 0.45},
            live | {"candidate": "Romeu Zema", "yes_price": 0.05},
            live | {"candidate": np.nan, "yes_price": 0.1},
            live | {"candidate": "Lula", "yes_price": np.nan},
            live | {"candidate": np.nan, "yes_price": 0.5, "active": False, "volume_usd": 0.0},  # placeholder
            live | {"candidate": np.nan, "yes_price": 0.5, "active": "True", "volume_usd": 0.0},  # never traded
        ]
    )
    p = sc.market_probabilities(snap, "first_place", ["Lula", "Flávio Bolsonaro"])
    assert p["Lula"] == pytest.approx(0.5) and p[sc.OTHER] == pytest.approx(0.15 / 1.2)
    assert sum(p.values()) == pytest.approx(1.0)
    assert sc.multiclass_brier(p, "Lula") == pytest.approx(0.25 + (0.45 / 1.2) ** 2 + (0.15 / 1.2) ** 2)
    assert sc.log_score({"Lula": 0.0}, "Lula") == pytest.approx(np.log(1e-4))
    assert sc.market_probabilities(snap, "election_winner", ["Lula"]) is None


def test_displayed_probabilities_remainder_and_rounding():
    def rows(vals):
        return pd.DataFrame(
            [
                {
                    "benchmark": "PollingData",
                    "event_kind": "pollingdata_win_probability",
                    "probability_event": "election_winner",
                    "candidate": c,
                    "value_displayed_pct": v,
                }
                for c, v in vals
            ]
        )

    p = sc.displayed_probabilities(rows([("Lula", 55.0), ("Flávio Bolsonaro", 40.0)]), "election_winner", NAMED[:2])
    assert p[sc.OTHER] == pytest.approx(0.05)
    p = sc.displayed_probabilities(rows([("Lula", 55.0), ("Flávio Bolsonaro", 46.0)]), "election_winner", NAMED[:2])
    assert sum(p.values()) == pytest.approx(1.0) and p[sc.OTHER] == 0.0
    with pytest.raises(ValueError, match="sum to"):
        sc.displayed_probabilities(rows([("Lula", 60.0), ("Flávio Bolsonaro", 46.0)]), "election_winner", NAMED[:2])


def test_event_probability_uses_the_registered_smoothing():
    d = np.array([[60.0, 30.0, 10.0], [40.0, 50.0, 10.0], [45.0, 44.0, 11.0], [52.0, 38.0, 10.0]])
    cats = ["A", "B", config.OTHERS_LABEL]
    assert sc.event_probability(d, cats, "first_place", "A") == pytest.approx(3.5 / 5)
    assert sc.event_probability(d, cats, "leader_above_50") == pytest.approx(2.5 / 5)
    with pytest.raises(ValueError):
        sc.event_probability(d, cats, "unknown")


def test_president_scores_use_the_registered_function():
    doc = {
        "alternatives": {
            "F_E": {
                "r1_model": "F",
                "h2h_model": "E",
                "primary": True,
                "probabilities": {"Lula": 0.6, "Flávio Bolsonaro": 0.4},
                "unmodelled": 0.0,
            }
        }
    }
    t = sc.score_president_doc(doc, "Flávio Bolsonaro")
    ref = score_president({"Lula": 0.6, "Flávio Bolsonaro": 0.4}, 0.0, "Flávio Bolsonaro")
    assert t.iloc[0]["brier"] == pytest.approx(ref["brier"]) and t.iloc[0]["p_winner"] == pytest.approx(0.4)


# ---------------------------------------------------------------- end to end (synthetic)
def test_score_script_end_to_end_on_a_synthetic_package(tmp_path):
    fz = _package(tmp_path / "freeze")
    res = _results_csv(tmp_path / "r.csv", votes_r2={"Lula": 51000, "Flávio Bolsonaro": 49000})
    s26 = _script("score_2026")
    out, doc = tmp_path / "out", tmp_path / "docs" / "scorecard-2026.md"
    common = ["--freeze-dir", str(fz), "--results", str(res), "--out-dir", str(out), "--doc", str(doc)]
    assert s26.main(["--stage", "r1", *common]) == 0
    r1 = json.loads((out / "scorecard_2026_r1.json").read_text(encoding="utf-8"))
    assert r1["package_status"] == "FINAL" and "warning" not in r1
    assert r1["observed"]["first_place"] == "Lula" and r1["observed"]["leader_above_50"] is False
    assert r1["criteria"]["L3"]["n_categories"] == len(CATS)
    assert r1["criteria"]["L1"]["assessed_against"].startswith("A (same categories)")
    bench = {(b["benchmark"], b["event"]): b for b in r1["benchmarks"]}
    assert bench[("Polymarket", "first_place")]["p_actual"] == pytest.approx(0.62 / 1.05)
    assert bench[("Polymarket", "first_round_outright_win")]["brier"] == pytest.approx(0.01)
    assert bench[("PollingData", "first_round_outright_win")]["status"].startswith("N/A")
    lula_first = bench[("PollingData", "first_place: Lula")]
    assert lula_first["outcome"] is True and lula_first["brier"] == pytest.approx(0.16**2)
    assert bench[("PollingData", "runoff_held")]["brier"] == pytest.approx(0.0)
    # model F on the same events, next to each benchmark
    cats, draws = freeze.load_draws(fz / freeze.DRAWS_FILE)
    p_lula = sc.event_probability(draws["F"], cats, "first_place", "Lula")
    assert lula_first["F_p_yes"] == pytest.approx(p_lula) and lula_first["F_brier"] == pytest.approx((p_lula - 1) ** 2)
    p_out = sc.event_probability(draws["F"], cats, "leader_above_50")
    assert bench[("PollingData", "runoff_held")]["F_p_yes"] == pytest.approx(1 - p_out)
    assert bench[("Polymarket", "first_round_outright_win")]["F_brier"] == pytest.approx(p_out**2)
    f_row = next(m for m in r1["models"] if m["model"] == "F")
    assert bench[("Polymarket", "first_place")]["F_brier"] == pytest.approx(f_row["brier_first"])
    assert s26.main(["--stage", "president", *common]) == 0
    pres = json.loads((out / "scorecard_2026_president.json").read_text(encoding="utf-8"))
    assert pres["elected"] == "Lula" and pres["how"] == "elected in the runoff"
    primary = next(c for c in pres["combinations"] if c["primary"])
    assert primary["p_winner"] == pytest.approx(0.55)
    text = doc.read_text(encoding="utf-8")
    assert "## First round" in text and "## Who was elected" in text and "L1" in text
    assert (out / "scorecard_2026_r1_categories.csv").exists() and (out / "scorecard_2026_r1.csv").exists()


def test_score_script_refuses_a_tampered_package(tmp_path):
    fz = _package(tmp_path / "freeze")
    (fz / "forecast.json").write_text("{}", encoding="utf-8")
    s26 = _script("score_2026")
    with pytest.raises(SystemExit, match="does not verify"):
        s26.main(
            [
                "--stage",
                "r1",
                "--freeze-dir",
                str(fz),
                "--results",
                str(_results_csv(tmp_path / "r.csv")),
                "--out-dir",
                str(tmp_path / "o"),
                "--doc",
                str(tmp_path / "d.md"),
            ]
        )
    assert not (tmp_path / "o" / "scorecard_2026_r1.json").exists()


def test_rehearsal_packages_are_labelled(tmp_path):
    fz = _package(tmp_path / "freeze", status="PRELIMINARY", pollingdata=False, polymarket=False)
    s26 = _script("score_2026")
    out = tmp_path / "out"
    s26.main(
        [
            "--stage",
            "r1",
            "--freeze-dir",
            str(fz),
            "--results",
            str(_results_csv(tmp_path / "r.csv")),
            "--out-dir",
            str(out),
            "--doc",
            str(tmp_path / "d.md"),
        ]
    )
    r1 = json.loads((out / "scorecard_2026_r1.json").read_text(encoding="utf-8"))
    assert "rehearsal" in r1["warning"] and r1["criteria"]["L1"]["assessed_against"].startswith("B only")
    assert all(b["status"].startswith("N/A") for b in r1["benchmarks"])


def test_outright_first_round_winner_is_elected_without_a_runoff(tmp_path):
    votes = dict(VOTES) | {"Lula": 54000, "Flávio Bolsonaro": 30000}
    s26 = _script("score_2026")
    assert s26.elected_candidate(_results_csv(tmp_path / "r.csv", votes)) == (
        "Lula",
        "elected in the first round (more than 50% of valid votes)",
    )

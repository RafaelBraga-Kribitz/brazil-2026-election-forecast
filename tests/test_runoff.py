"""Runoff pipeline: head-to-head information sets, h2h fits, LOEO h2h term and president probabilities.

Synthetic polls, cached fits and results only; `brfc.model.fit` is replaced by a deterministic fake (no MCMC)."""

from __future__ import annotations

import importlib.util
import inspect
import json
import zlib
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from brfc import conditional, config, election_day, figures, model, pipeline, runoff, scoring, transform
from brfc.provenance import sha256_file
from tests.conftest import poll_rows

VARIANT = "single_regime"
FORBIDDEN_PARTS = ("actual", "result", "outcome", "winner", "advanced", "pair_actual")
# historical results of OTHER elections feed the first-round LOEO term; the target is removed first (tested below)
ALLOWED = {"first_round_forecast_draws": {"results_hist"}, "first_round_forecast": {"results_hist"}}
FORECAST_FUNCTIONS = (
    runoff.h2h_rows,
    runoff.h2h_pairs,
    runoff.fit_h2h,
    runoff.h2h_forecast,
    runoff.first_round_forecast_draws,
    runoff.first_round_forecast,
    runoff.pair_win_probabilities,
    runoff.president_forecast,
    runoff.president_combinations,
)
H2H_PATH = (
    runoff.h2h_rows,
    runoff.h2h_pairs,
    runoff.fit_h2h,
    runoff.h2h_forecast,
    runoff.load_h2h,
    runoff._window_rows,
    runoff._scenario_sets,
    runoff._select_pair,
    runoff._pair_information_set,
)
PAIR_2022 = ("Jair Bolsonaro", "Lula")  # alphabetical key of the actual 2022 pair
PAIR_2018 = ("Fernando Haddad", "Jair Bolsonaro")
LATE_POLLSTERS = ["Quaest", "AtlasIntel", "Ipec", "Datafolha"]  # Datafolha fields last (baseline C at the eve)


def e1(election: str) -> date:
    return config.ELECTION_DATES[(election, 1)]


def h2h_poll_rows(election: str, pairs: dict, *, n: int = 12, first: int = 50, step: int = 4) -> list[dict]:
    """`n` polls (days first, first-step, ... before E1), each with one scenario per pair {pair: pair[0] share}."""
    rows = []
    for k in range(n):
        fe = e1(election) - timedelta(days=first - step * k)
        fs = fe - timedelta(days=2)
        pollster = LATE_POLLSTERS[k % len(LATE_POLLSTERS)]
        for (a, b), sa in pairs.items():
            share_a = sa + (k % 3) - 1.0
            rows += poll_rows(
                election,
                2,
                pollster,
                str(fs),
                str(fe),
                2000,
                {a: share_a, b: 92.0 - share_a},
                scenario=f"{a} vs {b}",
                blank_null=8.0,
            )
    return rows


def frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["round"] = df["round"].astype(int)
    return df


@pytest.fixture
def polls_2022() -> pd.DataFrame:
    rows = h2h_poll_rows("2022", {PAIR_2022: 42.0, ("Ciro Gomes", "Lula"): 30.0, ("Lula", "Sergio Moro"): 50.0})
    rows += h2h_poll_rows("2022", {("Jair Bolsonaro", "Simone Tebet"): 45.0}, n=5)  # too few polls
    for d in (5, 12):  # post-first-round runoff polls of the actual pair
        fe = e1("2022") + timedelta(days=d)
        rows += poll_rows(
            "2022", 2, "Datafolha", str(fe - timedelta(days=2)), str(fe), 3000, {"Jair Bolsonaro": 30, "Lula": 60}
        )
    return frame(rows)


def fake_fit_factory(calls: list | None = None, converge_on: int = 1):
    """Deterministic stand-in for brfc.model.fit (draws centred on the mean polled share of the series)."""

    log = [] if calls is None else calls

    def fake(wide, series, *, window_start, election_day, cutoff, two_regime=True, priors=None, sampler=None):
        log.append(
            {
                "series": list(series),
                "window_start": window_start,
                "election_day": election_day,
                "cutoff": cutoff,
                "two_regime": two_regime,
                "sampler": dict(sampler or {}),
            }
        )
        attempt = sum(1 for c in log if c["cutoff"] == cutoff and c["series"] == list(series))
        rng = np.random.default_rng(zlib.crc32(f"{series[0]}|{cutoff}".encode()))
        m = float(np.nanmean(wide[series[0]].to_numpy(dtype=float)))
        draws = m + 2.0 * rng.standard_normal((400, len(series)))
        ok = attempt >= converge_on
        diag = {
            "rhat_max": 1.0 if ok else 1.05,
            "ess_bulk_min": 1000.0,
            "ess_tail_min": 1000.0,
            "divergences": 0,
            "draws": 400,
            "converged": ok,
        }
        path = pd.DataFrame({"date": [election_day], "series": [series[0]], "mean": [m]})
        return model.FitResult(
            series=list(series),
            days=[window_start, election_day],
            election_day_draws=draws,
            path=path,
            house=pd.DataFrame(),
            params=pd.DataFrame(),
            diagnostics=diag,
            n_polls=int(wide["poll_id"].nunique()),
            cutoff=cutoff,
            variant="two_regime" if two_regime else "single_regime",
            polls=wide,
        )

    return fake


def boom(*a, **k):
    raise AssertionError("results or the actual runoff pair were read in a forecast code path")


# ---------------------------------------------------------------------------------------------------------------
# leakage: signatures, sources, results and future polls
# ---------------------------------------------------------------------------------------------------------------


def test_forecast_signatures_accept_no_outcome_argument():
    for f in FORECAST_FUNCTIONS:
        params = set(inspect.signature(f).parameters)
        bad = {p for p in params if any(s in p.lower() for s in FORBIDDEN_PARTS)} - ALLOWED.get(f.__name__, set())
        assert not bad, f"runoff.{f.__name__} accepts {bad}"


def test_h2h_path_sources_never_mention_results_or_actual_pairs():
    for f in H2H_PATH:
        text = inspect.getsource(f)
        for token in ("RUNOFF_PAIRS", "load_results", "actual_shares", "results", "elected_candidate", "winner"):
            assert token not in text, f"runoff.{f.__name__} mentions {token}"
    for f in (
        runoff.first_round_forecast,
        runoff.first_round_forecast_draws,
        runoff.president_forecast,
        runoff.president_combinations,
    ):
        text = inspect.getsource(f)
        for token in ("RUNOFF_PAIRS", "load_results", "elected_candidate"):
            assert token not in text, f"runoff.{f.__name__} mentions {token}"


def test_h2h_outputs_ignore_results_and_actual_pairs(monkeypatch, polls_2022, tmp_path):
    cutoff = runoff.h2h_cutoff("2022", 1)
    base_pairs = runoff.h2h_pairs(polls_2022, "2022", cutoff)
    base_rows = runoff.h2h_rows(polls_2022, "2022", PAIR_2022, cutoff)
    monkeypatch.setattr(model, "fit", fake_fit_factory())
    base_fit = runoff.fit_h2h(polls_2022, "2022", PAIR_2022, 1, VARIANT, cache=tmp_path / "a")

    import brfc.data as data

    monkeypatch.setattr(data, "load_results", boom)
    monkeypatch.setattr(data, "actual_shares", boom)
    for pairs in ({}, {"2022": ("Ciro Gomes", "Simone Tebet"), "2018": ("Lula", "Marina Silva")}):
        monkeypatch.setattr(config, "RUNOFF_PAIRS", pairs)
        assert runoff.h2h_pairs(polls_2022, "2022", cutoff) == base_pairs
        pd.testing.assert_frame_equal(runoff.h2h_rows(polls_2022, "2022", PAIR_2022, cutoff), base_rows)
        f = runoff.fit_h2h(polls_2022, "2022", PAIR_2022, 1, VARIANT, cache=tmp_path / f"b{len(pairs)}")
        assert f.meta["poll_ids"] == base_fit.meta["poll_ids"]
        np.testing.assert_array_equal(runoff.h2h_forecast(f), runoff.h2h_forecast(base_fit))


def test_polls_on_or_after_first_round_never_enter(polls_2022):
    late = runoff.runoff_day("2022") - timedelta(days=1)  # a cutoff after E1 still sees no post-E1 poll
    post = set(polls_2022.loc[pd.to_datetime(polls_2022["field_end"]).dt.date >= e1("2022"), "poll_id"])
    assert post
    rows = runoff.h2h_rows(polls_2022, "2022", PAIR_2022, late)
    assert pd.to_datetime(rows["field_end"]).dt.date.max() < e1("2022")
    assert not set(rows["poll_id"]) & post
    extra = frame(h2h_poll_rows("2022", {("Ciro Gomes", "Simone Tebet"): 40.0}, n=10, first=-2, step=1))
    assert pd.to_datetime(extra["field_end"]).dt.date.min() >= e1("2022")
    with_post = pd.concat([polls_2022, extra], ignore_index=True)
    assert runoff.h2h_pairs(with_post, "2022", late) == runoff.h2h_pairs(polls_2022, "2022", late)


def test_future_polls_cannot_change_information_set(polls_2022):
    """Perturb, add, overlap and publish-late polls after the cutoff: rows, pairs and dropped polls are unchanged."""
    h = 7
    cutoff = runoff.h2h_cutoff("2022", h)
    base_rows = runoff.h2h_rows(polls_2022, "2022", PAIR_2022, cutoff)
    base_pairs = runoff.h2h_pairs(polls_2022, "2022", cutoff)
    fe = pd.to_datetime(polls_2022["field_end"]).dt.date
    perturbed = polls_2022.copy()
    future = fe > cutoff
    perturbed.loc[future, "share_reported"] = perturbed.loc[future, "share_reported"] * 0.5 + 20
    # a later wave of every pollster that overlaps its earlier waves (fieldwork straddles the cutoff)
    wave = []
    for p in LATE_POLLSTERS:
        wave += poll_rows(
            "2022",
            2,
            p,
            str(cutoff - timedelta(days=12)),
            str(cutoff + timedelta(days=2)),
            1500,
            {"Jair Bolsonaro": 20, "Lula": 70},
            scenario="Jair Bolsonaro vs Lula",
        )
    # fieldwork before the cutoff but published after it
    late_pub = poll_rows(
        "2022",
        2,
        "Quaest",
        str(cutoff - timedelta(days=6)),
        str(cutoff - timedelta(days=1)),
        1800,
        {"Jair Bolsonaro": 10, "Lula": 80},
        scenario="Jair Bolsonaro vs Lula",
    )
    for r in late_pub:
        r["publication_date"] = str(cutoff + timedelta(days=1))
    new_pair = []  # a ballot pair polled 10 times, all after the cutoff
    for k in range(10):
        fe = str(cutoff + timedelta(days=1 + k % 5))
        new_pair += poll_rows(
            "2022", 2, f"P{k}", fe, fe, 1000, {"Ciro Gomes": 45, "Simone Tebet": 40}, scenario="Ciro vs Tebet"
        )
    alt = pd.concat([perturbed, frame(wave + late_pub + new_pair)], ignore_index=True)
    pd.testing.assert_frame_equal(runoff.h2h_rows(alt, "2022", PAIR_2022, cutoff), base_rows)
    assert runoff.h2h_pairs(alt, "2022", cutoff) == base_pairs
    _, drop_a = runoff._pair_information_set(runoff._window_rows(polls_2022, "2022"), PAIR_2022, cutoff)
    _, drop_b = runoff._pair_information_set(runoff._window_rows(alt, "2022"), PAIR_2022, cutoff)
    assert set(drop_a["poll_id"]) == set(drop_b["poll_id"])
    # once the overlapping waves are available, the dependence rule does drop earlier waves
    later = runoff.h2h_rows(alt, "2022", PAIR_2022, cutoff + timedelta(days=2))
    assert set(base_rows["poll_id"]) - set(later["poll_id"])


def test_h2h_rows_valid_vote_conversion_and_scenario(polls_2022):
    rows = runoff.h2h_rows(polls_2022, "2022", ("Lula", "Jair Bolsonaro"), runoff.h2h_cutoff("2022", 1))
    assert set(rows["candidate"]) == set(PAIR_2022)
    assert (rows["scenario"] == "Jair Bolsonaro vs Lula").all()
    sums = rows.groupby("poll_id")["valid_vote_share"].sum()
    np.testing.assert_allclose(sums.to_numpy(), 100.0)
    assert rows.groupby("poll_id").size().eq(2).all()


def test_h2h_pairs_are_alphabetical_ballot_pairs_with_enough_polls(polls_2022):
    pairs = runoff.h2h_pairs(polls_2022, "2022", runoff.h2h_cutoff("2022", 1))
    # Sergio Moro is not on the 2022 ballot; the Tebet pair has 5 polls < MIN_POLLS_PER_FIT
    assert pairs == [("Ciro Gomes", "Lula"), PAIR_2022]
    assert all(p == tuple(sorted(p)) for p in pairs)
    assert runoff.h2h_pairs(polls_2022, "2014", runoff.h2h_cutoff("2014", 1)) == []


# ---------------------------------------------------------------------------------------------------------------
# h2h fit and forecast
# ---------------------------------------------------------------------------------------------------------------


def test_fit_h2h_key_meta_and_retry_loop(monkeypatch, polls_2022, tmp_path):
    calls = []
    monkeypatch.setattr(model, "fit", fake_fit_factory(calls, converge_on=2))
    f = runoff.fit_h2h(polls_2022, "2022", ("Lula", "Jair Bolsonaro"), 7, VARIANT, tag="t", cache=tmp_path)
    assert f.key == "2022_h2h_jair-bolsonaro_vs_lula_r1h07_single_regime_t"
    for ext in (".json", ".npy", ".rows.csv", ".path.csv"):
        assert (tmp_path / f"{f.key}{ext}").exists()
    m = f.meta
    assert (m["round"], m["kind"], m["pair"], m["r1_horizon"]) == (2, "h2h", list(PAIR_2022), 7)
    assert m["cutoff"] == str(e1("2022") - timedelta(days=7)) and m["n_polls"] == len(m["poll_ids"]) == 11
    assert len(calls) == 2 and [a["attempt"] for a in m["attempts"]] == [1, 2] and m["diagnostics"]["converged"]
    c = calls[0]
    assert c["series"] == ["Jair Bolsonaro"] and c["two_regime"] is False
    assert c["window_start"] == e1("2022") - timedelta(days=config.WINDOW_DAYS_R1)
    assert c["election_day"] == runoff.runoff_day("2022") and c["cutoff"] == date.fromisoformat(m["cutoff"])
    assert calls[1]["sampler"] == model.ATTEMPTS[1]
    # cached: no new sampling; force refits
    g = runoff.fit_h2h(polls_2022, "2022", PAIR_2022, 7, VARIANT, tag="t", cache=tmp_path)
    assert len(calls) == 2 and g.meta["poll_ids"] == m["poll_ids"]
    runoff.fit_h2h(polls_2022, "2022", PAIR_2022, 7, VARIANT, tag="t", cache=tmp_path, force=True)
    assert len(calls) == 3


def test_fit_h2h_skips_pairs_with_too_few_polls(monkeypatch, polls_2022, tmp_path):
    monkeypatch.setattr(model, "fit", boom)
    pair = ("Jair Bolsonaro", "Simone Tebet")
    assert runoff.fit_h2h(polls_2022, "2022", pair, 1, VARIANT, cache=tmp_path) is None
    meta = json.loads((tmp_path / f"{runoff.h2h_key('2022', pair, 1, VARIANT)}.json").read_text(encoding="utf-8"))
    assert meta["status"] == f"skipped: 5 polls < {config.MIN_POLLS_PER_FIT}"
    assert runoff.load_h2h("2022", pair, 1, VARIANT, cache=tmp_path) is None


def _cached(pair, mean_first, election="2022", r1_horizon=1, n=2000, seed=0) -> pipeline.CachedFit:
    draws = mean_first + 2.0 * np.random.default_rng(seed).standard_normal((n, 1))
    meta = {
        "pair": list(pair),
        "series": [pair[0]],
        "r1_horizon": r1_horizon,
        "n_polls": 10,
        "election": election,
        "cutoff": str(runoff.h2h_cutoff(election, r1_horizon)),
        "diagnostics": {"converged": True},
    }
    return pipeline.CachedFit(f"{election}_h2h_{runoff.pair_slug(pair)}", meta, draws, pd.DataFrame())


def _devs(values: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"election": e, "round": "h2h", "role": "rank1", "deviation": v} for e, v in values.items()],
        columns=runoff.DEVIATION_COLUMNS,
    )


def test_h2h_forecast_is_deterministic_and_projected():
    f = _cached(PAIR_2022, 47.0)
    post = runoff.h2h_error_model(_devs({"2014": 1.0, "2018": -2.0}), "2022", "E", seed=5)
    a = runoff.h2h_forecast(f, post, seed=3)
    b = runoff.h2h_forecast(f, post, seed=3)
    np.testing.assert_array_equal(a, b)
    assert a.shape == (2000, 2) and np.allclose(a.sum(axis=1), 100.0) and a.min() >= 0.0
    assert not np.array_equal(a, runoff.h2h_forecast(f, post, seed=4))
    np.testing.assert_array_equal(runoff.h2h_forecast(f), runoff.h2h_forecast(f))


def test_h2h_term_follows_forecast_rank_not_pair_order():
    """Pair[0] trails in the latent forecast, so the rank-1 deviation (+) must move pair[1] up."""
    post = runoff.h2h_error_model(_devs({"2014": 4.0, "2018": 4.0}), "2022", "F", seed=1)
    f = _cached(PAIR_2022, 45.0)
    lat, adj = runoff.h2h_forecast(f), runoff.h2h_forecast(f, post, seed=2)
    assert adj[:, 1].mean() > lat[:, 1].mean() + 1.0


def test_h2h_deviations_hand_computed_and_restricted_to_actual_eve_fits():
    f = _cached(PAIR_2022, 47.0)  # Bolsonaro latent ~47 -> Lula is forecast rank 1 (~53)
    res = pd.DataFrame(
        {"election": "2022", "round": 2, "candidate": ["Lula", "Jair Bolsonaro"], "valid_share": [50.9, 49.1]}
    )
    d = runoff.h2h_deviations({"2022": f}, res)
    assert len(d) == 1 and d.iloc[0]["category"] == "Lula" and d.iloc[0]["round"] == "h2h"
    expected = 50.9 - (100.0 - np.clip(f.draws[:, 0], 0, 100).mean())
    assert d.iloc[0]["deviation"] == pytest.approx(expected)
    with pytest.raises(ValueError):
        runoff.h2h_deviations({"2022": _cached(("Ciro Gomes", "Lula"), 40.0)}, res)
    with pytest.raises(ValueError):
        runoff.h2h_deviations({"2022": _cached(PAIR_2022, 47.0, r1_horizon=7)}, res)


@pytest.mark.parametrize("variant", ["E", "F"])
def test_target_deviation_cannot_move_its_own_h2h_error_model(variant):
    devs = _devs({"2014": 1.0, "2018": -2.0, "2022": 3.0})
    poisoned = _devs({"2014": 1.0, "2018": -2.0, "2022": 99.0})
    a = runoff.h2h_error_model(devs, "2022", variant, seed=7)
    b = runoff.h2h_error_model(poisoned, "2022", variant, seed=7)
    np.testing.assert_array_equal(a.sigma_draws, b.sigma_draws)
    np.testing.assert_array_equal(a.mu_draws["rank1"], b.mu_draws["rank1"])
    assert a.train_elections == ["2014", "2018"]
    assert runoff.h2h_error_model(devs, "2026", variant, seed=7).train_elections == ["2014", "2018", "2022"]
    c = runoff.h2h_error_model(_devs({"2014": 1.0, "2018": 50.0, "2022": 3.0}), "2022", variant, seed=7)
    assert not np.array_equal(a.sigma_draws, c.sigma_draws)  # other elections do move it


def test_president_forecast_is_deterministic_and_complete(tmp_path):
    rng = np.random.default_rng(11)
    x = np.clip(np.array([46.0, 38.0, 9.0, 7.0]) + rng.normal(0, 4, size=(3000, 4)), 0, None)
    r1 = 100 * x / x.sum(axis=1, keepdims=True)
    cats = ["Lula", "Jair Bolsonaro", "Ciro Gomes", "Others"]
    fits = {PAIR_2022: _cached(PAIR_2022, 48.0), ("Ciro Gomes", "Lula"): _cached(("Ciro Gomes", "Lula"), 40.0)}
    post = runoff.h2h_error_model(_devs({"2018": 2.0, "2022": -1.5}), "2026", "E", seed=runoff.SEED_2026)
    a = runoff.president_forecast(r1, cats, fits, post, seed=1, n_polls={("Ciro Gomes", "Jair Bolsonaro"): 3})
    b = runoff.president_forecast(
        r1.copy(), cats, dict(fits), post, seed=1, n_polls={("Ciro Gomes", "Jair Bolsonaro"): 3}
    )
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert sum(a["probabilities"].values()) + a["unmodelled"] == pytest.approx(1.0, abs=1e-9)
    pairs = {p["pair"]: p for p in a["pairs"]}
    assert pairs["Jair Bolsonaro vs Lula"]["modelled"] and pairs["Jair Bolsonaro vs Lula"]["share_first"]["candidate"]
    unmod = pairs.get("Ciro Gomes vs Jair Bolsonaro")
    if unmod is not None:
        assert not unmod["modelled"] and unmod["win"] is None and unmod["n_polls"] == 3
    # the document built from it renders with the president figure
    doc = {"status": "PRELIMINARY", "cutoff": "2026-09-29", "label": runoff.validation_label(2)} | a
    path = tmp_path / "president_2026.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert figures.n_validated_elections(doc["label"]) == 2
    assert figures.n_validated_elections(runoff.validation_label(1)) == 1
    out = figures.president_probability(out_dir=tmp_path, json_path=path)
    assert out.read_bytes()[:4] == b"\x89PNG"


# ---------------------------------------------------------------------------------------------------------------
# first-round draws and the end-to-end backtest (synthetic cache)
# ---------------------------------------------------------------------------------------------------------------

R1_SERIES = {
    "2014": (["Dilma Rousseff", "Marina Silva", "Aécio Neves"], [40.0, 27.0, 25.0]),
    "2018": (["Jair Bolsonaro", "Fernando Haddad", "Ciro Gomes", "Geraldo Alckmin"], [40.0, 25.0, 13.0, 8.0]),
    "2022": (["Lula", "Jair Bolsonaro", "Ciro Gomes", "Simone Tebet"], [47.0, 38.0, 6.0, 5.0]),
}
RESULTS = {
    ("2014", 1): {"Dilma Rousseff": 41.6, "Aécio Neves": 33.6, "Marina Silva": 21.3, "Luciana Genro": 3.5},
    ("2014", 2): {"Dilma Rousseff": 51.6, "Aécio Neves": 48.4},
    ("2018", 1): {
        "Jair Bolsonaro": 46.0,
        "Fernando Haddad": 29.3,
        "Ciro Gomes": 12.5,
        "Geraldo Alckmin": 4.8,
        "João Amoêdo": 7.4,
    },
    ("2018", 2): {"Jair Bolsonaro": 55.1, "Fernando Haddad": 44.9},
    ("2022", 1): {
        "Lula": 48.4,
        "Jair Bolsonaro": 43.2,
        "Simone Tebet": 4.2,
        "Ciro Gomes": 3.0,
        "Soraya Thronicke": 1.2,
    },
    ("2022", 2): {"Lula": 50.9, "Jair Bolsonaro": 49.1},
}
H2H_POLLS = {
    "2018": {PAIR_2018: 40.0, ("Ciro Gomes", "Jair Bolsonaro"): 41.0, ("Ciro Gomes", "Fernando Haddad"): 48.0},
    "2022": {PAIR_2022: 42.0, ("Ciro Gomes", "Lula"): 30.0},
}


def synthetic_results(shift: dict | None = None) -> pd.DataFrame:
    rows = []
    for (e, r), shares in RESULTS.items():
        for c, s in shares.items():
            s = s + (shift or {}).get((e, r, c), 0.0)
            rows.append({"election": e, "round": r, "candidate": c, "votes": int(s * 1e5)})
    res = pd.DataFrame(rows)
    res["valid_share"] = 100.0 * res["votes"] / res.groupby(["election", "round"])["votes"].transform("sum")
    return res.assign(source_url="https://example.org/r", source_revision="1")


def _r1_rows(election: str, cutoff: date) -> pd.DataFrame:
    named, means = R1_SERIES[election]
    rows = []
    for k, days in enumerate((20, 16, 12, 9, 5, 2)):
        fe = e1(election) - timedelta(days=days)
        if fe > cutoff:
            continue
        shares = {c: m * 0.85 + 0.5 * (k % 2) for c, m in zip(named, means, strict=True)}
        shares["__others__"] = 90.0 - sum(shares.values())
        p = ["Datafolha", "Quaest", "AtlasIntel"][k % 3]
        rows += poll_rows(election, 1, p, str(fe - timedelta(days=2)), str(fe), 2500, shares, blank_null=10.0)
    return transform.to_valid_votes(frame(rows))


def write_r1_cache(cache, horizons=(1, 7)) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    for e, (named, means) in R1_SERIES.items():
        series = [*named, config.OTHERS_LABEL]
        for h in horizons:
            key = pipeline.fit_key(e, 1, h, VARIANT)
            rng = np.random.default_rng(zlib.crc32(key.encode()))
            mu = np.array([*means, 100.0 - sum(means)])
            draws = np.clip(mu + rng.normal(0, 2.5 + 0.2 * h, size=(2000, len(series))), 0.1, None)
            cutoff = runoff.h2h_cutoff(e, h)
            rows = _r1_rows(e, cutoff)
            meta = {
                "key": key,
                "election": e,
                "round": 1,
                "horizon": h,
                "variant": VARIANT,
                "tag": "",
                "cutoff": str(cutoff),
                "named": named,
                "series": series,
                "n_polls": int(rows["poll_id"].nunique()),
                "n_pollsters": int(rows["pollster"].nunique()),
                "status": "ok",
                "diagnostics": {"converged": True},
            }
            np.save(cache / f"{key}.npy", draws.astype(np.float32))
            rows.to_csv(cache / f"{key}.rows.csv", index=False)
            (cache / f"{key}.json").write_text(json.dumps(meta), encoding="utf-8")


def historical_h2h_polls() -> pd.DataFrame:
    rows = []
    for e, pairs in H2H_POLLS.items():
        rows += h2h_poll_rows(e, pairs)
    return frame(rows)


@pytest.fixture(scope="module")
def backtest_cache(tmp_path_factory):
    """Synthetic caches shared by the backtest tests: first-round fits in one directory and every listed h2h fit
    (fake sampler) in a separate h2h directory, as pipeline.CACHE and runoff.H2H_CACHE."""
    root = tmp_path_factory.mktemp("runoff")
    cache, h2h_cache = root / "fits", root / "fits_h2h"
    write_r1_cache(cache)
    polls = historical_h2h_polls()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(model, "fit", fake_fit_factory())
        for e in config.HISTORICAL:
            for h in runoff.H2H_HORIZONS:
                for p in runoff.h2h_pairs(polls, e, runoff.h2h_cutoff(e, h)):
                    assert runoff.fit_h2h(polls, e, p, h, VARIANT, cache=h2h_cache) is not None
    return cache, polls, h2h_cache


@pytest.fixture(scope="module")
def base_backtest(backtest_cache):
    cache, polls, h2h_cache = backtest_cache
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(model, "fit", boom)  # evaluation must never sample
        return runoff.evaluate_backtest(VARIANT, synthetic_results(), polls=polls, cache=cache, h2h_cache=h2h_cache)


def test_first_round_draws_reproduce_pipeline_backtest(monkeypatch, backtest_cache):
    monkeypatch.setattr(model, "fit", boom)
    cache, *_ = backtest_cache
    res = synthetic_results()
    monkeypatch.setattr(pipeline, "CACHE", cache)
    orig = pipeline.load_fit
    monkeypatch.setattr(pipeline, "load_fit", lambda key, cache=cache: orig(key, cache))
    scores = pipeline.evaluate_backtest(VARIANT, res)[0]
    from brfc.data import actual_shares

    for e in config.HISTORICAL:
        for h in runoff.H2H_HORIZONS:
            for m in ("F", "E", "E0"):
                d, cats = runoff.first_round_forecast_draws(e, h, VARIANT, res, m, cache=cache)
                s = scoring.score(d, None, cats, actual_shares(res, e, 1, cats), 1)
                row = scores[
                    (scores.election == e) & (scores["round"] == 1) & (scores.horizon == h) & (scores.model == m)
                ].iloc[0]
                for k in ("mae", "log_first", "brier_first", "coverage_80", "margin_error"):
                    assert s[k] == pytest.approx(row[k], abs=1e-12), (e, h, m, k)


def test_first_round_draws_ignore_the_target_results(backtest_cache):
    cache, *_ = backtest_cache
    res = synthetic_results()
    d, _ = runoff.first_round_forecast_draws("2022", 1, VARIANT, res, "F", cache=cache)
    no_target = res[res["election"] != "2022"]
    shifted = synthetic_results({("2022", 1, "Lula"): 10.0, ("2022", 2, "Lula"): -5.0})
    for r in (no_target, shifted):
        np.testing.assert_array_equal(runoff.first_round_forecast_draws("2022", 1, VARIANT, r, "F", cache=cache)[0], d)
    moved = synthetic_results({("2018", 1, "Jair Bolsonaro"): 10.0})
    assert not np.array_equal(runoff.first_round_forecast_draws("2022", 1, VARIANT, moved, "F", cache=cache)[0], d)
    with pytest.raises(FileNotFoundError):
        runoff.first_round_forecast_draws("2022", 14, VARIANT, res, "F", cache=cache)


def test_evaluate_backtest_end_to_end(monkeypatch, backtest_cache, base_backtest):
    cache, polls, h2h_cache = backtest_cache
    monkeypatch.setattr(model, "fit", boom)
    assert not list(cache.glob("*_h2h_*")) and list(h2h_cache.glob("*_h2h_*.json"))  # separate caches
    out = base_backtest
    assert set(out) == {"runoff", "runoff_categories", "president", "deviations", "error_models"}
    r = out["runoff"]
    ok = r[r["status"] != "N/A: h2h fit not in the cache (run scripts/run_runoff_backtest.py --stage fits)"]
    assert set(ok["election"]) == {"2018", "2022"}
    assert (r.loc[r["election"] == "2014", "status"].str.startswith("N/A")).all()
    e_rows = r[r["model"] == "E"].set_index(["election", "r1_horizon"])
    assert e_rows.loc[("2018", 1), "train_elections"] == "2022"
    assert e_rows.loc[("2022", 7), "train_elections"] == "2018"
    assert r.loc[r["primary"].eq(True), "model"].unique().tolist() == ["E"]
    b = r[(r["model"] == "B") & (r["election"] == "2022") & (r["r1_horizon"] == 1)].iloc[0]
    assert b["train_elections"] == "2018" and b["loeo_rmse"] > 0
    devs = out["deviations"]
    assert sorted(devs["election"]) == ["2018", "2022"] and (devs["round"] == "h2h").all()
    p = out["president"]
    scored = p[p["status"] == "ok"]
    assert set(scored["model"]) == {"F_E", "F_F", "E_E", "B", "C"} and set(scored["election"]) == {"2018", "2022"}
    assert scored["p_winner"].between(0, 1).all() and (scored["unmodelled"] >= 0).all()
    for _, row in scored.iterrows():
        assert sum(json.loads(row["probabilities_json"]).values()) + row["unmodelled"] == pytest.approx(1.0, abs=1e-9)
    assert (p.loc[p["election"] == "2014", "status"].str.startswith("N/A")).all()
    # deterministic
    again = runoff.evaluate_backtest(VARIANT, synthetic_results(), polls=polls, cache=cache, h2h_cache=h2h_cache)
    for k in out:
        pd.testing.assert_frame_equal(out[k], again[k])


def test_evaluate_backtest_h2h_term_is_loeo(monkeypatch, backtest_cache, base_backtest):
    cache, polls, h2h_cache = backtest_cache
    monkeypatch.setattr(model, "fit", boom)
    base = base_backtest
    shifted = runoff.evaluate_backtest(
        VARIANT, synthetic_results({("2022", 2, "Lula"): 4.0}), polls=polls, cache=cache, h2h_cache=h2h_cache
    )
    em_a, em_b = base["error_models"], shifted["error_models"]
    cols = ["sigma_mean", "mu_rank1_mean", "train_elections"]
    pd.testing.assert_frame_equal(
        em_a.loc[em_a["election"] == "2022", cols], em_b.loc[em_b["election"] == "2022", cols]
    )
    assert not em_a.loc[em_a["election"] == "2018", "sigma_mean"].equals(
        em_b.loc[em_b["election"] == "2018", "sigma_mean"]
    )


def test_elected_candidate():
    res = synthetic_results()
    assert [runoff.elected_candidate(res, e) for e in config.HISTORICAL] == ["Dilma Rousseff", "Jair Bolsonaro", "Lula"]
    one_round = res[~((res["election"] == "2022") & (res["round"] == 2))]
    with pytest.raises(ValueError):
        runoff.elected_candidate(one_round, "2022")  # no runoff recorded and nobody above 50%


def test_election_day_apply_matches_conditional_complement():
    """Round-2 projection used by h2h_forecast keeps the two shares complementary (sanity for win_given_pair)."""
    f = _cached(PAIR_2022, 50.0, n=500, seed=4)
    d = runoff.h2h_forecast(f)
    w = conditional.win_given_pair(d, PAIR_2022)
    assert w[PAIR_2022[0]] == pytest.approx(np.mean(d[:, 0] > 50.0) + 0.5 * np.mean(d[:, 0] == 50.0))
    np.testing.assert_array_equal(d, election_day.apply(f.draws, list(PAIR_2022), {}, None, 2))


# ---------------------------------------------------------------------------------------------------------------
# combinations, separate cache, repair, determinism, criteria, sensitivity and the 2026 script
# ---------------------------------------------------------------------------------------------------------------


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(f"script_{name}", config.ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_president_backtest_reports_every_combination(base_backtest):
    p = base_backtest["president"]
    for (e, h), g in p.groupby(["election", "r1_horizon"]):
        assert sorted(g["model"]) == sorted(["F_E", "F_F", "E_E", "B", "C"]), (e, h)
    combos = p[p["model"].isin(["F_E", "F_F", "E_E"])]
    assert (combos["model"] == combos["r1_model"] + "_" + combos["h2h_model"]).all()
    assert combos.loc[combos["primary"].eq(True), "model"].unique().tolist() == ["F_E"]
    ok = p[(p["status"] == "ok") & (p["election"] == "2022") & (p["r1_horizon"] == 1)].set_index("model")
    assert ok.loc["E_E", "r1_train_elections"] == ok.loc["F_E", "r1_train_elections"] == "2014+2018"
    assert ok.loc["E_E", "h2h_train_elections"] == ok.loc["F_E", "h2h_train_elections"] == "2018"
    # F x E and F x F share the first-round draws; E x E uses first-round model E draws
    assert ok.loc["F_E", "p_actual_pair"] == ok.loc["F_F", "p_actual_pair"]
    assert ok.loc["E_E", "probabilities_json"] != ok.loc["F_E", "probabilities_json"]


def test_evaluation_passes_only_other_elections_results_to_forecasts(monkeypatch, backtest_cache):
    cache, polls, h2h_cache = backtest_cache
    monkeypatch.setattr(model, "fit", boom)
    orig, seen = runoff.first_round_forecast, []

    def spy(election, r1_horizon, variant, results_hist, ed_model="F", **kw):
        got = set(results_hist["election"].astype(str))
        assert election not in got, f"results of {election} passed to its own first-round forecast"
        seen.append((election, ed_model))
        return orig(election, r1_horizon, variant, results_hist, ed_model, **kw)

    monkeypatch.setattr(runoff, "first_round_forecast", spy)
    runoff.evaluate_backtest(VARIANT, synthetic_results(), polls=polls, cache=cache, h2h_cache=h2h_cache)
    assert set(seen) == {(e, m) for e in config.HISTORICAL for m in ("F", "E")}


def test_h2h_fits_have_their_own_cache():
    assert runoff.H2H_CACHE == config.DATA / "cache" / "fits_h2h"
    assert runoff.H2H_CACHE != pipeline.CACHE and pipeline.CACHE not in runoff.H2H_CACHE.parents
    for f in (runoff.fit_h2h, runoff.load_h2h):
        assert inspect.signature(f).parameters["cache"].default == runoff.H2H_CACHE, f.__name__
    params = inspect.signature(runoff.evaluate_backtest).parameters
    assert params["h2h_cache"].default == runoff.H2H_CACHE and params["cache"].default == pipeline.CACHE


def _write_meta(cache, key: str, **meta) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"{key}.json").write_text(json.dumps({"key": key, **meta}), encoding="utf-8")


R1_META = {
    "election": "2018",
    "round": 2,
    "horizon": 7,
    "variant": VARIANT,
    "tag": "",
    "status": "ok",
    "named": ["Jair Bolsonaro"],
    "n_polls": 15,
    "n_pollsters": 5,
    "n_dropped_overlap": 0,
    "diagnostics": {"converged": False},
}


def _h2h_meta(**kw) -> dict:
    base = {
        "kind": "h2h",
        "election": "2022",
        "round": 2,
        "horizon": 1,
        "r1_horizon": 1,
        "variant": VARIANT,
        "tag": "",
        "pair": list(PAIR_2022),
        "status": "ok",
        "named": ["Jair Bolsonaro"],
        "n_polls": 20,
        "n_pollsters": 4,
        "n_dropped_overlap": 0,
        "diagnostics": {"converged": False},
        "attempts": [{"attempt": 1, "converged": False}, {"attempt": 2, "converged": False}],
    }
    return base | kw


def test_first_round_repair_and_diagnostics_skip_h2h_fits(monkeypatch, tmp_path):
    """Defence in depth: even inside pipeline.CACHE, an h2h fit is never repaired or listed by the round scripts."""
    r1_key = pipeline.fit_key("2018", 2, 7, VARIANT)
    _write_meta(tmp_path, r1_key, **R1_META)
    _write_meta(tmp_path, runoff.h2h_key("2022", PAIR_2022, 1, VARIANT), **_h2h_meta())
    fits_script = load_script("run_backtest_fits")
    assert fits_script.repair_jobs(tmp_path) == [("2018", 2, 7, VARIANT, True, "", None, True, 1, 2)]
    ev = load_script("evaluate_backtest")
    monkeypatch.setattr(ev, "CACHE", tmp_path)
    assert ev.diagnostics()["key"].tolist() == [r1_key]


def test_runoff_repair_stage_selects_non_converged_h2h_fits(tmp_path):
    rb = load_script("run_runoff_backtest")
    three = [{"attempt": k, "converged": False} for k in (1, 2, 3)]
    tebet = ("Jair Bolsonaro", "Simone Tebet")
    _write_meta(tmp_path, runoff.h2h_key("2022", PAIR_2022, 1, VARIANT), **_h2h_meta())  # needs attempt 3
    _write_meta(tmp_path, runoff.h2h_key("2022", PAIR_2022, 7, VARIANT), **_h2h_meta(r1_horizon=7, attempts=three))
    _write_meta(
        tmp_path,
        runoff.h2h_key("2022", ("Ciro Gomes", "Lula"), 1, VARIANT),
        **_h2h_meta(pair=["Ciro Gomes", "Lula"], diagnostics={"converged": True}),
    )
    _write_meta(tmp_path, runoff.h2h_key("2022", PAIR_2022, 1, VARIANT, "t"), **_h2h_meta(tag="t"))
    _write_meta(
        tmp_path,
        runoff.h2h_key("2026", ("Flávio Bolsonaro", "Lula"), 6, VARIANT),
        **_h2h_meta(election="2026", pair=["Flávio Bolsonaro", "Lula"], r1_horizon=6),
    )
    _write_meta(tmp_path, runoff.h2h_key("2022", tebet, 1, VARIANT), kind="h2h", status="skipped: 5 polls < 8")
    _write_meta(tmp_path, pipeline.fit_key("2018", 2, 7, VARIANT), **R1_META)  # not an h2h fit
    assert rb.repair_jobs(tmp_path) == [("2022", PAIR_2022, 1, VARIANT, True, rb.REPAIR_START_ATTEMPT)]
    assert model.ATTEMPTS[rb.REPAIR_START_ATTEMPT] == model.ATTEMPTS[-1]


def test_fit_h2h_repair_runs_the_third_attempt(monkeypatch, polls_2022, tmp_path):
    calls = []
    monkeypatch.setattr(model, "fit", fake_fit_factory(calls, converge_on=99))  # never converges
    f = runoff.fit_h2h(polls_2022, "2022", PAIR_2022, 1, VARIANT, cache=tmp_path)
    assert len(calls) == len(model.ATTEMPTS) and runoff.converged(f) is False
    rb = load_script("run_runoff_backtest")
    assert rb.repair_jobs(tmp_path) == []  # the third attempt already ran
    path = tmp_path / f"{f.key}.json"  # an older cache entry that stopped after two attempts
    meta = json.loads(path.read_text(encoding="utf-8"))
    meta["attempts"] = meta["attempts"][:2]
    path.write_text(json.dumps(meta), encoding="utf-8")
    (e, pair, h, variant, force, start), *rest = rb.repair_jobs(tmp_path)
    assert not rest and (e, pair, h, force, start) == ("2022", PAIR_2022, 1, True, 2)
    calls.clear()
    g = runoff.fit_h2h(polls_2022, e, pair, h, variant, cache=tmp_path, force=force, start_attempt=start)
    assert len(calls) == 1 and calls[0]["sampler"] == model.ATTEMPTS[2]
    assert [a["attempt"] for a in g.meta["attempts"]] == [1, 2, 3]
    assert rb.repair_jobs(tmp_path) == []
    with pytest.raises(ValueError):
        runoff.fit_h2h(polls_2022, "2022", PAIR_2022, 1, VARIANT, cache=tmp_path, force=True, start_attempt=3)


def _assert_same_fit(a: pipeline.CachedFit, b: pipeline.CachedFit) -> None:
    assert a.key == b.key and a.meta == b.meta
    assert a.draws.dtype == b.draws.dtype
    np.testing.assert_array_equal(a.draws, b.draws)
    pd.testing.assert_frame_equal(a.kept_rows, b.kept_rows)


def test_fresh_and_cached_fits_are_bit_identical(monkeypatch, polls_2022, fixture_2022_r1, tmp_path):
    monkeypatch.setattr(model, "fit", fake_fit_factory())
    fresh = runoff.fit_h2h(polls_2022, "2022", PAIR_2022, 1, VARIANT, cache=tmp_path / "h2h")
    monkeypatch.setattr(model, "fit", boom)  # the second call must come from the cache
    cached = runoff.fit_h2h(polls_2022, "2022", PAIR_2022, 1, VARIANT, cache=tmp_path / "h2h")
    _assert_same_fit(fresh, cached)
    np.testing.assert_array_equal(fresh.draws, fresh.draws.astype(np.float32).astype(float))  # stored precision
    post = runoff.h2h_error_model(_devs({"2014": 1.0, "2018": -2.0}), "2022", "E", seed=5)
    np.testing.assert_array_equal(runoff.h2h_forecast(fresh, post, seed=1), runoff.h2h_forecast(cached, post, seed=1))

    monkeypatch.setattr(model, "fit", fake_fit_factory())
    fresh1 = pipeline.fit_one(fixture_2022_r1, "2022", 1, 7, VARIANT, cache=tmp_path / "r1")
    monkeypatch.setattr(model, "fit", boom)
    cached1 = pipeline.fit_one(fixture_2022_r1, "2022", 1, 7, VARIANT, cache=tmp_path / "r1")
    _assert_same_fit(fresh1, cached1)
    np.testing.assert_array_equal(fresh1.draws, fresh1.draws.astype(np.float32).astype(float))


def test_success_criteria_hand_computed():
    def rows(model_name, values, h=1):
        return [
            {"election": e, "r1_horizon": h, "model": model_name, **v}
            for e, v in zip(config.HISTORICAL, values, strict=True)
        ]

    runoff_scores = pd.DataFrame(
        rows("E", [{"mae": 1.0}, {"mae": 2.0}, {"mae": 3.0}])
        + rows("E", [{"mae": 0.0}] * 3, h=7)  # T-7 is not the criterion cutoff
        + rows("B", [{"mae": 2.0}, {"mae": 2.0}, {"mae": np.nan}])  # 2022: no B score
    )
    cats = pd.DataFrame(
        [
            {"election": e, "r1_horizon": 1, "model": "E", "category": c, "in94": inside}
            for e, inside in zip(config.HISTORICAL, (True, False, True), strict=True)
            for c in ("a", "b")
        ]
    )
    pres = pd.DataFrame(
        rows("F_E", [{"brier": 0.5}, {"brier": 0.2}, {"brier": 0.3}])
        + rows("F_F", [{"brier": 0.0}] * 3)
        + rows("B", [{"brier": 0.1}, {"brier": 0.2}, {"brier": 0.3}])
    )
    out = {
        "runoff": runoff_scores,
        "runoff_categories": cats,
        "president": pres,
        "deviations": pd.DataFrame({"election": ["2014"], "variant": [VARIANT]}),
    }
    c = runoff.success_criteria(out)
    hr1, hr2, hr3 = c["HR1_E_h2h_mae_le_B"], c["HR2_E_94_interval"], c["HR3_FxE_brier_le_B"]
    assert hr1["elections"] == ["2014", "2018"] and hr1["E"] == pytest.approx(1.5) and hr1["B"] == pytest.approx(2.0)
    assert hr1["met"] is True and hr1["result"] == "met"
    assert hr2["inside_94"] == 2 and hr2["n_elections"] == 3 and hr2["met"] is True
    assert hr2["by_election"] == {"2014": True, "2018": False, "2022": True}
    assert hr3["F_E"] == pytest.approx(1.0 / 3.0) and hr3["B"] == pytest.approx(0.2)
    assert hr3["met"] is False and hr3["result"] == "not met"
    assert c["variant"] == VARIANT
    json.dumps(c)
    c0 = runoff.success_criteria({k: v.iloc[0:0] for k, v in out.items()})
    assert all(c0[k]["result"] == "not evaluable" for k in ("HR1_E_h2h_mae_le_B", "HR2_E_94_interval"))


def test_success_criteria_from_the_backtest(base_backtest):
    c = runoff.success_criteria(base_backtest)
    for k in ("HR1_E_h2h_mae_le_B", "HR2_E_94_interval", "HR3_FxE_brier_le_B"):
        assert isinstance(c[k]["met"], bool) and c[k]["result"] in ("met", "not met"), k
    hr1 = c["HR1_E_h2h_mae_le_B"]
    r = base_backtest["runoff"]
    e_eve = r[(r["model"] == "E") & (r["r1_horizon"] == 1)].set_index("election")
    assert hr1["elections"] and set(hr1["elections"]) <= {"2018", "2022"}
    assert hr1["E"] == pytest.approx(e_eve.loc[hr1["elections"], "mae"].mean())
    assert c["HR2_E_94_interval"]["n_elections"] == 2  # 2014 has no h2h fit in the synthetic cache
    json.dumps(c)


def test_runoff_sensitivity_varies_only_the_h2h_error_scale(monkeypatch, backtest_cache, base_backtest):
    cache, polls, h2h_cache = backtest_cache
    monkeypatch.setattr(model, "fit", boom)
    kw = {"polls": polls, "cache": cache, "h2h_cache": h2h_cache}
    s = runoff.sensitivity_backtest(VARIANT, synthetic_results(), base=base_backtest, **kw)
    assert list(s.columns) == runoff.SENSITIVITY_COLUMNS
    assert dict(zip(s["setting"], s["sigma_scale"], strict=True)) == {"primary": 3.0, "sig1.5": 1.5, "sig6": 6.0}
    assert set(s["table"]) == {"h2h", "president"} and (s["variant"] == VARIANT).all()
    w = s[(s["table"] == "h2h") & (s["model"] == "E") & (s["r1_horizon"] == 1)].set_index("setting")["width_94"]
    assert w["sig1.5"] < w["primary"] < w["sig6"]
    for table, m in (("h2h", "E0"), ("h2h", "B"), ("president", "B"), ("president", "C")):
        x = s[(s["table"] == table) & (s["model"] == m)]
        vals = x.drop(columns=["setting", "sigma_scale"]).groupby("r1_horizon").nunique(dropna=False)
        assert (vals <= 1).all().all(), (table, m)
    fe = s[(s["table"] == "president") & (s["model"] == "F_E")]
    assert fe["brier"].nunique() > 1
    primary = runoff.sensitivity_summary(base_backtest, "primary")
    got = s[s["setting"] == "primary"].reset_index(drop=True)
    pd.testing.assert_frame_equal(got[primary.columns], primary.reset_index(drop=True), check_dtype=False)


def _r1_block(seed: int, shift: float) -> dict:
    rng = np.random.default_rng(seed)
    x = np.clip(np.array([46.0 + shift, 38.0, 9.0, 7.0]) + rng.normal(0, 4, size=(3000, 4)), 0, None)
    cats = ["Lula", "Jair Bolsonaro", "Ciro Gomes", "Others"]
    return {"draws": 100 * x / x.sum(axis=1, keepdims=True), "categories": cats}


def _combos_2026() -> dict[str, dict]:
    r1 = {"F": _r1_block(11, 0.0), "E": _r1_block(12, -1.0)}
    fits = {PAIR_2022: _cached(PAIR_2022, 48.0), ("Ciro Gomes", "Lula"): _cached(("Ciro Gomes", "Lula"), 40.0)}
    devs = _devs({"2014": 1.0, "2018": 2.0, "2022": -1.5})
    posts = {v: runoff.h2h_error_model(devs, "2026", v, seed=runoff.SEED_2026 + (v == "F")) for v in ("E", "F")}
    combos = runoff.president_combinations(r1, fits, posts, seed=runoff.SEED_2026)
    assert list(combos) == ["F_E", "F_F", "E_E"]
    f, e = r1["F"], r1["E"]
    expected = {
        "F_E": runoff.president_forecast(f["draws"], f["categories"], fits, posts["E"], seed=runoff.SEED_2026),
        "F_F": runoff.president_forecast(f["draws"], f["categories"], fits, posts["F"], seed=runoff.SEED_2026),
        "E_E": runoff.president_forecast(e["draws"], e["categories"], fits, posts["E"], seed=runoff.SEED_2026),
    }
    for k, v in expected.items():
        assert json.dumps(combos[k], sort_keys=True) == json.dumps(v, sort_keys=True), k
    combos["E0"] = runoff.president_forecast(f["draws"], f["categories"], fits, None)
    return combos


def test_2026_alternatives_contain_every_combination():
    script = load_script("forecast_2026_president")
    combos = _combos_2026()
    alt = script.alternatives_block(combos)
    assert set(alt) == {"F_E", "F_F", "E_E", "E0"}
    assert [k for k, v in alt.items() if v["primary"]] == ["F_E"]
    assert {k: (v["r1_model"], v["h2h_model"]) for k, v in alt.items()} == {
        "F_E": ("F", "E"),
        "F_F": ("F", "F"),
        "E_E": ("E", "E"),
        "E0": ("F", "E0"),
    }
    assert alt["E_E"]["probabilities"] != alt["F_E"]["probabilities"]
    assert alt["F_F"]["outright"] == alt["F_E"]["outright"] != alt["E_E"]["outright"]
    for v in alt.values():
        assert sum(v["probabilities"].values()) + v["unmodelled"] == pytest.approx(1.0, abs=1e-9)
    json.dumps(alt)

    t = script.president_table(combos, "FINAL", "2026-10-03")
    assert list(t.columns) == script.TABLE_COLUMNS and set(t["combination"]) == set(combos)
    for label, r in combos.items():
        g = t[t["combination"] == label]
        elected = g[g["component"] == "elected"].set_index("candidate")["probability"].to_dict()
        assert elected == pytest.approx(r["probabilities"])
        parts = g[g["component"].isin(["outright", "runoff"])].groupby("candidate")["probability"].sum()
        for c, p in r["probabilities"].items():
            assert parts.get(c, 0.0) == pytest.approx(p, abs=1e-12)
        assert g.loc[g["component"] == "unmodelled", "probability"].item() == pytest.approx(r["unmodelled"])


def test_2026_president_freeze_files_are_hashed(tmp_path):
    script = load_script("forecast_2026_president")
    combos = _combos_2026()
    fz = tmp_path / "freeze"
    (fz / "sub").mkdir(parents=True)
    (fz / "forecast.json").write_text('{"status": "FINAL"}\n', encoding="utf-8")
    (fz / "sub" / "extra.csv").write_text("a\n1\n", encoding="utf-8")
    first = f"{sha256_file(fz / 'forecast.json')}  forecast.json\n"
    (fz / "forecast_hash.txt").write_text(first, encoding="utf-8")
    doc = {"status": "FINAL", "probabilities": combos["F_E"]["probabilities"], "alternatives": {}}
    table = script.president_table(combos, "FINAL", "2026-10-03")
    lines = script.write_freeze(fz, doc, table)
    assert (fz / "forecast_hash.txt").read_text(encoding="utf-8").splitlines() == lines
    entries = {name: sha for sha, name in (x.split("  ", 1) for x in lines)}
    assert set(entries) == {"forecast.json", "president.json", "president.csv", "sub/extra.csv"}
    assert all(sha256_file(fz / n) == h for n, h in entries.items())
    assert json.loads((fz / "president.json").read_text(encoding="utf-8")) == doc
    assert len(pd.read_csv(fz / "president.csv")) == len(table)
    script.write_freeze(fz, doc | {"generated_utc": "later"}, table)  # its own files may be rewritten
    (fz / "forecast.json").write_text('{"status": "changed"}\n', encoding="utf-8")
    with pytest.raises(SystemExit, match=r"forecast\.json"):
        script.write_freeze(fz, doc, table)


def test_2026_president_preflight(monkeypatch, tmp_path):
    script = load_script("forecast_2026_president")
    monkeypatch.setattr(script, "DEVIATIONS", tmp_path / "h2h_deviations.csv")
    monkeypatch.setattr(model, "fit", boom)
    argv = ["forecast_2026_president.py", "--cutoff", "2026-10-03"]
    monkeypatch.setattr("sys.argv", [*argv, "--status", "FINAL"])
    with pytest.raises(SystemExit, match=r"h2h_deviations\.csv"):
        script.main()
    monkeypatch.setattr("sys.argv", [*argv, "--status", "PRELIMINARY", "--freeze-dir", str(tmp_path)])
    with pytest.raises(SystemExit, match="FINAL"):
        script.main()


def test_evaluate_stage_writes_criteria_and_sensitivity(monkeypatch, backtest_cache, tmp_path):
    import functools

    import brfc.data as data

    cache, polls, h2h_cache = backtest_cache
    monkeypatch.setattr(model, "fit", boom)
    rb = load_script("run_runoff_backtest")
    monkeypatch.setattr(rb, "OUT", tmp_path)
    monkeypatch.setattr(data, "load_results", synthetic_results)
    monkeypatch.setattr(data, "load_polls", lambda elections=config.HISTORICAL: polls)
    orig = runoff.evaluate_backtest
    monkeypatch.setattr(runoff, "evaluate_backtest", functools.partial(orig, cache=cache, h2h_cache=h2h_cache))
    rb.evaluate(VARIANT)
    crit = json.loads((tmp_path / "success_criteria_runoff.json").read_text(encoding="utf-8"))
    assert {"HR1_E_h2h_mae_le_B", "HR2_E_94_interval", "HR3_FxE_brier_le_B"} <= set(crit)
    assert crit["variant"] == VARIANT and all(crit[k]["result"] in ("met", "not met") for k in crit if k[:2] == "HR")
    sens = pd.read_csv(tmp_path / "runoff_sensitivity.csv")
    assert set(sens["setting"]) == {"primary", "sig1.5", "sig6"}
    pres = pd.read_csv(tmp_path / "president_backtest.csv", dtype={"election": str})
    assert {"F_E", "F_F", "E_E", "B", "C"} == set(pres["model"])
    for name in ("runoff_backtest.csv", "runoff_backtest_categories.csv", "h2h_deviations.csv", "h2h_error_models.csv"):
        assert (tmp_path / name).exists(), name

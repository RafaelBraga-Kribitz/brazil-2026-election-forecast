"""Post-first-round 2026 runoff forecast (scripts/forecast_2026_runoff.py): offline, synthetic data only.

`brfc.model.fit` is replaced by a deterministic stand-in (no MCMC). The 2026 ballot is patched to placeholder names
and every poll share and result is synthetic."""

from __future__ import annotations

import importlib.util
import json
import zlib
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brfc import config, model, pipeline
from brfc.provenance import sha256_file
from tests.conftest import poll_rows

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("forecast_2026_runoff", ROOT / "scripts" / "forecast_2026_runoff.py")
fr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fr)

VARIANT = "single_regime"
BALLOT = ["Candidate A", "Candidate B", "Candidate C"]
PAIR = ("Candidate B", "Candidate A")  # as typed on the command line: first place first
POLLSTERS = ["Datafolha", "Quaest", "Ipec", "AtlasIntel", "PoderData"]
HIST_SHARES = {"2014": 47.0, "2018": 50.0, "2022": 46.0}  # first-listed candidate, of 92% stated intentions
HIST_ACTUAL = {"2014": 51.6, "2018": 55.1, "2022": 50.9}  # synthetic runoff valid shares of the first-listed
E1, E2 = config.ELECTION_DATES[("2026", 1)], config.ELECTION_DATES[("2026", 2)]
N_EVE_POLLS = 21  # 20 daily polls of the pair plus one published late
FREEZE_FILES = {
    "runoff.json",
    "runoff.csv",
    "poll_snapshot.csv",
    "baseline_snapshot.csv",
    "MODEL_VERSION.txt",
    "forecast_hash.txt",
}


def boom(*args, **kwargs):
    raise AssertionError("data were read before the command line was validated")


def frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["round"] = df["round"].astype(int)
    return df


def daily_runoff_polls(election: str, pair: tuple[str, str], first_share: float) -> list[dict]:
    """One-day polls of `pair` every day from the day after the first round to the runoff eve (rotating
    pollsters, so the dependence rule drops nothing)."""
    rows = []
    day = config.ELECTION_DATES[(election, 1)] + timedelta(days=1)
    k = 0
    while day < config.ELECTION_DATES[(election, 2)]:
        a = first_share + ((k % 3) - 1) * 0.8
        rows += poll_rows(
            election,
            2,
            POLLSTERS[k % len(POLLSTERS)],
            str(day),
            str(day),
            2000,
            {pair[0]: a, pair[1]: 92.0 - a},
            scenario=f"{pair[0]} vs {pair[1]}",
            blank_null=8.0,
        )
        day += timedelta(days=1)
        k += 1
    return rows


def polls_2026(n_days: int | None = None) -> pd.DataFrame:
    rows = daily_runoff_polls("2026", PAIR, 47.0)
    if n_days is not None:  # keep only the first n_days polls of the pair
        rows = [r for r in rows if date.fromisoformat(r["field_end"]) <= E1 + timedelta(days=n_days)]
        return frame(rows)
    late = poll_rows("2026", 2, "LatePub", "2026-10-10", "2026-10-10", 1500, {PAIR[0]: 40.0, PAIR[1]: 52.0})
    for r in late:
        r["publication_date"] = "2026-10-20"
    other_pair = daily_runoff_polls("2026", ("Candidate A", "Candidate C"), 60.0)  # not the advancing pair
    before_r1 = []  # head-to-head polls fielded before the first round
    for k in range(10):
        fe = str(E1 - timedelta(days=3 + 2 * k))
        before_r1 += poll_rows("2026", 2, f"H{k}", fe, fe, 1000, {PAIR[0]: 20.0, PAIR[1]: 72.0})
    return frame(rows + late + other_pair + before_r1)


def results_frame(with_2026: bool = False) -> pd.DataFrame:
    rows = []
    for e, share in HIST_ACTUAL.items():
        a, b = config.RUNOFF_PAIRS[e]
        rows += [
            {"election": e, "round": 2, "candidate": a, "votes": share * 1e6},
            {"election": e, "round": 2, "candidate": b, "votes": (100.0 - share) * 1e6},
        ]
    if with_2026:  # a result that must never be used
        for r in (1, 2):
            rows += [
                {"election": "2026", "round": r, "candidate": PAIR[0], "votes": 1.0},
                {"election": "2026", "round": r, "candidate": PAIR[1], "votes": 99.0},
            ]
    df = pd.DataFrame(rows)
    df["valid_share"] = 100.0 * df["votes"] / df.groupby(["election", "round"])["votes"].transform("sum")
    return df.assign(source_url="https://example.org/r", source_revision="1")


def fake_fit_factory(calls: list):
    """Deterministic stand-in for brfc.model.fit; records the 2026 runoff pair configured at call time."""

    def fake(wide, series, *, window_start, election_day, cutoff, two_regime=True, priors=None, sampler=None):
        calls.append(
            {
                "series": list(series),
                "election_day": election_day,
                "window_start": window_start,
                "cutoff": cutoff,
                "pair_2026": config.RUNOFF_PAIRS.get("2026"),
            }
        )
        rng = np.random.default_rng(zlib.crc32(f"{series[0]}|{cutoff}".encode()))
        m = float(np.nanmean(wide[series[0]].to_numpy(dtype=float)))
        diag = {
            "rhat_max": 1.0,
            "ess_bulk_min": 1000.0,
            "ess_tail_min": 1000.0,
            "divergences": 0,
            "draws": 500,
            "converged": True,
        }
        return model.FitResult(
            series=list(series),
            days=[window_start, election_day],
            election_day_draws=m + 1.5 * rng.standard_normal((500, len(series))),
            path=pd.DataFrame({"date": [election_day], "series": [series[0]], "mean": [m]}),
            house=pd.DataFrame(),
            params=pd.DataFrame(),
            diagnostics=diag,
            n_polls=int(wide["poll_id"].nunique()),
            cutoff=cutoff,
            variant="two_regime" if two_regime else "single_regime",
            polls=wide,
        )

    return fake


@pytest.fixture
def env(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(model, "fit", fake_fit_factory(calls))
    monkeypatch.setitem(config.BALLOTS, "2026", list(BALLOT))
    state = {"polls": polls_2026(), "results": results_frame(), "calls": calls, "tmp": tmp_path}
    hist_polls = frame([r for e, s in HIST_SHARES.items() for r in daily_runoff_polls(e, config.RUNOFF_PAIRS[e], s)])

    def load_polls(elections=("2026",)):
        assert tuple(elections) == ("2026",), "only 2026 polls are loaded"
        return state["polls"]

    monkeypatch.setattr(fr, "load_polls", load_polls)
    monkeypatch.setattr(fr, "load_results", lambda: state["results"])
    monkeypatch.setattr(fr, "REVISION", tmp_path / "no_revision.json")
    monkeypatch.setattr(fr, "REGIME", tmp_path / "no_regime.json")
    monkeypatch.setattr(fr, "BENCHMARKS", tmp_path / "no_benchmarks.csv")
    monkeypatch.setattr(fr, "FIRST_ROUND_PACKAGE", tmp_path / "freeze")
    monkeypatch.setattr(fr, "git_commit", lambda: "0" * 40)

    def use(name: str) -> Path:
        """Point the script at <tmp>/<name>/ (cache with historical round-2 fits, output JSON)."""
        cache = tmp_path / name / "cache"
        if not cache.exists():
            for e in config.HISTORICAL:
                for h in config.HORIZONS_R2:
                    pipeline.fit_one(hist_polls, e, 2, h, VARIANT, cache=cache)
        monkeypatch.setattr(fr, "CACHE", cache)
        monkeypatch.setattr(fr, "OUT_JSON", tmp_path / name / "runoff_2026.json")
        return tmp_path / name

    state["use"] = use
    return state


def run(env, name: str, *extra: str, status: str = "PRELIMINARY", cutoff: str = "2026-10-24") -> dict:
    env["use"](name)
    return fr.main(["--status", status, "--cutoff", cutoff, "--pair", *PAIR, "--variant", VARIANT, *extra])


def without_time(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k != "generated_utc"}


def test_pair_is_required_and_validated_before_any_data_is_read(env, monkeypatch, capsys):
    monkeypatch.setattr(fr, "load_polls", boom)
    monkeypatch.setattr(fr, "load_results", boom)
    base = ["--status", "FINAL", "--cutoff", "2026-10-24", "--variant", VARIANT]
    with pytest.raises(SystemExit) as exc:
        fr.main(base)
    assert exc.value.code == 2
    assert "--pair is required" in capsys.readouterr().err
    bad = [
        [*base, "--pair", "Candidate A"],  # one name only
        [*base, "--pair", "Candidate A", "candidate a"],  # the same candidate twice
        [*base, "--pair", "Candidate A", "Someone Else"],  # not on the 2026 ballot
        ["--status", "PRELIMINARY", "--cutoff", "2026-10-04", "--pair", *PAIR],  # first round not yet counted
        ["--status", "FINAL", "--cutoff", "2026-10-20", "--pair", *PAIR],  # FINAL uses the registered cutoff
        ["--status", "PRELIMINARY", "--cutoff", "2026-10-24", "--pair", *PAIR, "--freeze-dir", "x"],  # FINAL only
    ]
    for argv in bad:
        with pytest.raises(SystemExit) as exc:
            fr.main(argv)
        assert exc.value.code == 2, argv
    assert "2026" not in config.RUNOFF_PAIRS
    # names resolve accent- and case-insensitively to the ballot spelling, in the order given
    assert fr.resolve_pair(["candidate b", "CANDIDATE A"]) == PAIR


def test_runoff_pair_is_set_for_the_run_only(env, monkeypatch):
    before = dict(config.RUNOFF_PAIRS)
    assert "2026" not in before
    doc = run(env, "a")
    fit_calls = [c for c in env["calls"] if c["election_day"] == E2]
    assert fit_calls and all(c["pair_2026"] == PAIR and c["series"] == [PAIR[0]] for c in fit_calls)
    assert all(c["window_start"] == E1 + timedelta(days=1) for c in fit_calls)
    assert before == config.RUNOFF_PAIRS
    assert doc["runoff_pair"] == list(PAIR) and doc["modelled_series"] == [PAIR[0]]

    env["polls"] = polls_2026(n_days=5)  # too few polls: the fit is skipped and the run stops
    with pytest.raises(SystemExit, match="skipped"):
        run(env, "b")
    assert before == config.RUNOFF_PAIRS

    monkeypatch.setitem(config.RUNOFF_PAIRS, "2026", ("Candidate C", "Candidate A"))
    with pytest.raises(RuntimeError, match="inside"), fr.runoff_pair(PAIR):
        assert config.RUNOFF_PAIRS["2026"] == PAIR
        raise RuntimeError("inside")
    assert config.RUNOFF_PAIRS["2026"] == ("Candidate C", "Candidate A")


def test_output_schema_and_freeze_package(env):
    fz = env["tmp"] / "freeze_runoff"
    doc = run(env, "a", "--freeze-dir", str(fz), status="FINAL")
    for k in (
        "status",
        "quantity",
        "label",
        "cutoff",
        "information_cutoff_date",
        "horizon_days",
        "runoff_pair",
        "runoff_pair_source",
        "probabilities",
        "alternatives",
        "models",
        "error_models",
        "election_day_term_trained_on",
        "fit_diagnostics",
        "poll_source",
    ):
        assert k in doc, k
    assert doc["status"] == "FINAL" and doc["registered_freeze_utc"] == "2026-10-25T01:00:00Z"
    assert (doc["cutoff"], doc["horizon_days"], doc["primary_model"]) == ("2026-10-24", 1, "F")
    assert doc["label"] == "pre-registered; validated on 3 elections only"
    assert doc["quantity"] == "probability of being elected under this model"
    assert doc["election_day_term_trained_on"] == list(config.HISTORICAL)
    assert {v["train_elections"] for v in doc["error_models"].values()} == {"2014+2018+2022"}
    assert doc["n_polls"] == N_EVE_POLLS  # neither pre-first-round nor other-pair polls
    assert set(doc["models"]) == {"F", "E", "E0", "B", "C"}
    for m, spec in doc["models"].items():
        rows = spec["categories"]
        assert [r["category"] for r in rows] == list(PAIR), m
        assert sum(r["p_win"] for r in rows) == pytest.approx(1.0)
        assert all(r["q03"] <= r["q10"] <= r["median"] <= r["q90"] <= r["q97"] for r in rows)
        assert sum(r["mean"] for r in rows) == pytest.approx(100.0)
    for b in ("B", "C"):
        spec = doc["models"][b]
        assert spec["status"] == "calibrated probabilistic conversion of point baseline"
        assert spec["error_horizon_used"] == 1 and spec["train_elections"] == "2014+2018+2022"
        assert sum(spec["point"].values()) == pytest.approx(100.0)
    assert doc["probabilities"] == {r["category"]: r["p_win"] for r in doc["models"]["F"]["categories"]}
    assert set(doc["alternatives"]) == {"E", "E0"}

    text = fr.OUT_JSON.read_text(encoding="utf-8")
    assert json.loads(text, parse_constant=boom) == doc  # strict JSON: no NaN or Infinity

    assert {p.name for p in fz.iterdir()} == FREEZE_FILES
    assert json.loads((fz / "runoff.json").read_text(encoding="utf-8")) == doc
    lines = (fz / "forecast_hash.txt").read_text(encoding="utf-8").splitlines()
    hashed = dict(reversed(x.split("  ", 1)) for x in lines)
    assert set(hashed) == FREEZE_FILES - {"forecast_hash.txt"}
    assert all(sha256_file(fz / n) == h for n, h in hashed.items())
    csv = pd.read_csv(fz / "runoff.csv")
    assert set(csv["model"]) == {"F", "E", "E0", "B", "C"} and set(csv["category"]) == set(PAIR)
    snap = pd.read_csv(fz / "poll_snapshot.csv")
    assert set(snap["candidate"]) == set(PAIR) and snap["poll_id"].nunique() == N_EVE_POLLS
    assert pd.to_datetime(snap["field_end"]).dt.date.min() > E1

    first_round = env["tmp"] / "freeze"  # the frozen first-round package is never written to
    first_round.mkdir()
    (first_round / "forecast.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit):
        run(env, "a", "--freeze-dir", str(first_round), status="FINAL")
    assert [p.name for p in first_round.iterdir()] == ["forecast.json"]


def test_information_cutoff_and_baseline_horizon(env):
    doc = run(env, "a", cutoff="2026-10-15")
    assert doc["horizon_days"] == 10 and doc["registered_freeze_utc"] is None
    assert doc["n_polls"] == 11  # 2026-10-05..2026-10-15; the poll published on 2026-10-20 is not yet available
    assert {doc["models"][b]["error_horizon_used"] for b in ("B", "C")} == {7}


def test_deterministic_and_never_uses_2026_results(env):
    def n_2026_fits() -> int:
        return sum(c["election_day"] == E2 for c in env["calls"])

    first = without_time(run(env, "a"))
    assert without_time(run(env, "b")) == first  # fresh cache
    n = n_2026_fits()
    assert without_time(run(env, "a")) == first  # cached fit, not refitted
    assert n_2026_fits() == n
    env["results"] = results_frame(with_2026=True)
    assert without_time(run(env, "c")) == first  # 2026 rows are dropped before any use
    n_fits = len(env["calls"])
    env["polls"] = env["polls"][env["polls"]["pollster"] != "LatePub"]  # the poll table changed: refit
    assert run(env, "a")["n_polls"] == N_EVE_POLLS - 1
    assert len([c for c in env["calls"][n_fits:] if c["election_day"] == E2]) == 1
    with pytest.raises(ValueError, match="only historical results"):
        fr.forecast_runoff(
            env["polls"], env["results"], PAIR, date(2026, 10, 24), variant=VARIANT, tag="t", cache=fr.CACHE
        )


def test_all_2026_figures_add_the_president_figure_when_its_json_exists(monkeypatch, tmp_path):
    from brfc import figures

    calls = []
    names = ("posterior_forecast", "uncertainty_intervals", "house_effects", "president_probability")
    for name in names:
        monkeypatch.setattr(figures, name, lambda *a, _n=name, **k: calls.append(_n))
    monkeypatch.setattr(config, "OUTPUTS", tmp_path)
    monkeypatch.setattr(config, "FIGURES", tmp_path / "figures")
    figures.all_2026()
    assert calls == list(names[:3])
    (tmp_path / "president_2026.json").write_text("{}", encoding="utf-8")
    calls.clear()
    figures.all_2026()
    assert calls == list(names)

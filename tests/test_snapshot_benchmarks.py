"""Benchmark snapshot parsing (scripts/snapshot_benchmarks.py): offline, small inline fixtures, no network."""

from __future__ import annotations

import importlib.util
import json
import socket
from pathlib import Path

import pytest

from brfc.provenance import SourceRecord, sha256_bytes, write_record

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("snapshot_benchmarks", ROOT / "scripts" / "snapshot_benchmarks.py")
sb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sb)

TS = "2026-10-04T01:00:05Z"
SHA = "a" * 64


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted in an offline test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def _market(mid, question, prices='["0.4", "0.6"]', outcomes='["Yes", "No"]', **kw):
    m = {"id": mid, "question": question, "outcomes": outcomes, "outcomePrices": prices, "volume": "1000.5"}
    return m | kw


WINNER_EVENT = [
    {
        "id": "45915",
        "slug": "brazil-presidential-election",
        "title": "Brazil Presidential Election",
        "markets": [
            _market(
                "601819",
                "Will Luiz Inácio Lula da Silva win the 2026 Brazilian presidential election?",
                bestBid=0.4,
                bestAsk=0.41,
                groupItemTitle="Luiz Inácio Lula da Silva",
                closed=False,
                active=True,
            ),
            _market(
                "601826",
                "Will Flavio Bolsonaro win the 2026 Brazilian presidential election?",
                prices='["0.5975", "0.4025"]',
                bestBid="0.597",
                bestAsk="0.598",
                closed=False,
            ),
            _market("601835", "Will Pablo Marçal win the 2026 Brazilian presidential election?", bestAsk=0.001),
            _market("601820", "Will Jair Bolsonaro win the 2026 Brazilian presidential election?"),
            _market("601837", "Will Person O win the 2026 Brazilian presidential election?", prices=None, volume=0),
            _market("601849", "Will another person win the 2026 Brazilian presidential election?", prices=None),
        ],
    }
]


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Will Lula win the most votes in the first round?", "Lula"),
        ("Will Luiz Inacio Lula da Silva win?", "Lula"),
        ("Will Flavio Bolsonaro win?", "Flávio Bolsonaro"),  # accent-insensitive
        ("Will FLÁVIO BOLSONARO win?", "Flávio Bolsonaro"),
        ("Will Clariana Barao win?", "Clariana Barão"),
        ("Will Zema win the most votes?", "Romeu Zema"),
        ("Will Samara win the most votes?", "Samara Martins"),
        ("Will Escritor Augusto Cury win?", "Augusto Cury"),
        ("Will Veterinário Wilson Grassi win?", "Wilson Grassi"),
        ("Will Pablo Marçal win the most votes in the first round?", None),  # revoked candidacy
        ("Will Pablo Marcal win?", None),
        ("Will Jair Bolsonaro win?", None),  # not on the 2026 ballot; must not map to another Bolsonaro
        ("Will another candidate win?", None),
        ("Will Candidate A win?", None),
        ("Will Lula or Romeu Zema win?", None),  # ambiguous
    ],
)
def test_candidate_mapping(question, expected):
    assert sb._candidate(question) == expected


def test_election_winner_keeps_every_market_row():
    rows = sb.parse_polymarket_events(
        WINNER_EVENT, "election_winner", sb.EVENTS["election_winner"], TS, SHA, archive_url="https://arch.example/x"
    )
    assert [r["market_id"] for r in rows] == ["601819", "601826", "601835", "601820", "601837", "601849"]
    by_id = {r["market_id"]: r for r in rows}
    lula = by_id["601819"]
    assert lula["question"] == "Will Luiz Inácio Lula da Silva win the 2026 Brazilian presidential election?"
    assert lula["candidate"] == "Lula"
    assert lula["event_id"] == "45915"
    assert lula["event_title"] == "Brazil Presidential Election"
    assert lula["outcome_label"] == "Luiz Inácio Lula da Silva"
    assert (lula["yes_price"], lula["best_bid"], lula["best_ask"]) == (0.4, 0.4, 0.41)
    assert lula["volume_usd"] == 1000.5
    assert lula["closed"] is False
    assert (lula["retrieved_utc"], lula["raw_sha256"]) == (TS, SHA)
    assert lula["url"] == "https://polymarket.com/event/brazil-presidential-election"
    assert {r["archive_url"] for r in rows} == {"https://arch.example/x"}
    assert by_id["601826"]["candidate"] == "Flávio Bolsonaro"
    assert (by_id["601826"]["yes_price"], by_id["601826"]["best_bid"]) == (0.5975, 0.597)
    assert by_id["601835"]["candidate"] is None  # revoked candidacy
    assert by_id["601835"]["best_bid"] is None and by_id["601835"]["best_ask"] == 0.001
    assert by_id["601820"]["candidate"] is None
    assert by_id["601837"]["yes_price"] is None and by_id["601837"]["volume_usd"] == 0.0
    assert by_id["601849"]["candidate"] is None


def test_archive_url_empty_by_default_and_single_event_object_accepted():
    rows = sb.parse_polymarket_events(WINNER_EVENT[0], "election_winner", "s", TS, SHA)
    assert len(rows) == 6
    assert {r["archive_url"] for r in rows} == {""}


def test_outright_event_has_no_candidate_and_yes_price_follows_outcome_order():
    ev = [
        {
            "id": "45924",
            "title": "Will any presidential candidate win outright in the first round of the Brazil election?",
            "markets": [_market("601920", "Will any candidate win outright?", '["0.9395", "0.0605"]', '["No", "Yes"]')],
        }
    ]
    (row,) = sb.parse_polymarket_events(ev, "first_round_outright_win", "slug", TS, SHA)
    assert row["candidate"] == ""
    assert row["yes_price"] == 0.0605


def test_unknown_event_kind_is_rejected():
    with pytest.raises(ValueError, match="unknown event kind"):
        sb.parse_polymarket_events(WINNER_EVENT, "runoff_winner", "s", TS, SHA)


def test_event_id_problems():
    rows = sb.parse_polymarket_events(WINNER_EVENT, "election_winner", "s", TS, SHA)
    assert sb.event_id_problems(rows) == []
    moved = [WINNER_EVENT[0] | {"id": "999"}]
    rows = sb.parse_polymarket_events(moved, "election_winner", "s", TS, SHA)
    assert sb.event_id_problems(rows) == ["election_winner: event id 999 (expected 45915)"]


def _save_raw(raw_dir: Path, kind: str, label: str, payload: list) -> bytes:
    blob = json.dumps(payload).encode("utf-8")
    raw, rec = sb._raw_paths(raw_dir, kind, label)
    raw.write_bytes(blob)
    write_record(SourceRecord("polymarket_gamma", "https://x", TS, "api_response", None, sha256_bytes(blob), "p"), rec)
    return blob


def test_load_polymarket_reparses_saved_response_and_checks_hash(tmp_path):
    blob = _save_raw(tmp_path, "election_winner", "t", WINNER_EVENT)
    rows = sb.load_polymarket("t", ["election_winner"], {"election_winner": "https://a"}, raw_dir=tmp_path)
    assert len(rows) == 6
    assert {r["raw_sha256"] for r in rows} == {sha256_bytes(blob)}
    assert {r["retrieved_utc"] for r in rows} == {TS}
    assert {r["archive_url"] for r in rows} == {"https://a"}
    raw, _ = sb._raw_paths(tmp_path, "election_winner", "t")
    raw.write_bytes(blob + b" ")
    with pytest.raises(ValueError, match="SHA-256"):
        sb.load_polymarket("t", ["election_winner"], raw_dir=tmp_path)
    with pytest.raises(FileNotFoundError):
        sb.load_polymarket("t", ["first_place"], raw_dir=tmp_path)


def _pd(**kw) -> dict:
    d = {
        "retrieved_utc": "2026-10-04T01:02:00Z",
        "url": "https://pollingdata.example/2026",
        "basis": "total_incl_nao_valido",
        "values_pct": {"Lula": 40.8, "Flavio Bolsonaro": 30.0, "Outros": 19.8},
        "nao_valido_pct": 9.4,
        "last_poll_date": "2026-10-03",
    }
    return d | kw


def test_pollingdata_valid_share_from_nao_valido():
    rows = sb.parse_pollingdata(_pd())
    assert {r["event_kind"] for r in rows} == {"share"}
    by = {r["candidate_displayed"]: r for r in rows}
    assert by["Lula"]["valid_share_pct"] == pytest.approx(100.0 * 40.8 / 90.6)
    assert by["Lula"]["value_displayed_pct"] == 40.8
    assert by["Lula"]["nao_valido_pct"] == 9.4
    assert by["Flavio Bolsonaro"]["candidate"] == "Flávio Bolsonaro"
    assert by["Outros"]["candidate"] is None
    assert sum(r["valid_share_pct"] for r in rows) == pytest.approx(100.0)


def test_pollingdata_valid_basis_only_renormalises_rounding():
    rows = sb.parse_pollingdata(_pd(basis="valid", values_pct={"Lula": 50.1, "Romeu Zema": 50.0}, nao_valido_pct=None))
    by = {r["candidate"]: r["valid_share_pct"] for r in rows}
    assert by["Lula"] == pytest.approx(100.0 * 50.1 / 100.1)
    assert {r["nao_valido_pct"] for r in rows} == {None}


def test_candidates_not_displayed_are_not_imputed():
    rows = sb.parse_pollingdata(_pd(values_pct={"Lula": 40.0, "Romeu Zema": None}, nao_valido_pct=10.0))
    assert [r["candidate"] for r in rows] == ["Lula"]
    assert rows[0]["valid_share_pct"] == pytest.approx(100.0 * 40.0 / 90.0)
    partial = sb.parse_pollingdata(_pd(basis="valid", values_pct={"Lula": 45.0, "Romeu Zema": 40.0}))
    assert [r["valid_share_pct"] for r in partial] == [45.0, 40.0]  # no mass moved to the displayed names


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"nao_valido_pct": None}, "nao_valido_pct"),
        ({"basis": "total"}, "basis"),
        ({"values_pct": {"Lula": 101.0}}, "outside"),
        ({"values_pct": {"Lula": "40,8"}}, "number"),
        ({"values_pct": {"Lula": 60.0, "Romeu Zema": 40.0}}, "exceed"),
        ({"retrieved_utc": "sat night"}, "ISO-8601"),
        ({"url": ""}, "missing"),
    ],
)
def test_pollingdata_rejects_malformed_input(change, message):
    with pytest.raises(ValueError, match=message):
        sb.parse_pollingdata(_pd(**change))


FORECAST = {
    "url": "https://pollingdata.example/2026/forecast",
    "retrieved_utc": "2026-10-04T01:05:00Z",
    "win_probability_pct": {"Lula": 55.0, "Flávio Bolsonaro": 44.0, "Romeu Zema": None},
    "first_round_outright_pct": None,
    "method_note": "as displayed",
}


def test_no_forecast_rows_without_a_forecast_object():
    assert all(r["event_kind"] == "share" for r in sb.parse_pollingdata(_pd()))
    assert all(r["event_kind"] == "share" for r in sb.parse_pollingdata(_pd(forecast=None)))


def test_forecast_rows_only_for_displayed_values():
    rows = [r for r in sb.parse_pollingdata(_pd(forecast=FORECAST)) if r["event_kind"] != "share"]
    assert {r["event_kind"] for r in rows} == {"pollingdata_win_probability"}
    assert [(r["probability_event"], r["candidate"], r["value_displayed_pct"]) for r in rows] == [
        ("election_winner", "Lula", 55.0),
        ("election_winner", "Flávio Bolsonaro", 44.0),
    ]
    assert {r["retrieved_utc"] for r in rows} == {"2026-10-04T01:05:00Z"}
    assert {r["url"] for r in rows} == {FORECAST["url"]}
    assert {r["methodology_note"] for r in rows} == {"as displayed"}


def test_forecast_outright_row_when_displayed():
    f = FORECAST | {"win_probability_pct": {}, "first_round_outright_pct": 12.5}
    rows = [r for r in sb.parse_pollingdata(_pd(forecast=f)) if r["event_kind"] != "share"]
    assert [(r["probability_event"], r["candidate"], r["value_displayed_pct"]) for r in rows] == [
        ("first_round_outright_win", "", 12.5)
    ]


def test_forecast_needs_provenance_and_valid_values():
    with pytest.raises(ValueError, match="forecast"):
        sb.parse_pollingdata(_pd(forecast={"win_probability_pct": {"Lula": 50.0}}))
    with pytest.raises(ValueError, match="sum"):
        sb.parse_pollingdata(_pd(forecast=FORECAST | {"win_probability_pct": {"Lula": 70.0, "Romeu Zema": 40.0}}))


def test_pollingdata_file_is_optional(tmp_path, capsys):
    assert sb.pollingdata("freeze", manual_dir=tmp_path) == []
    assert "not found" in capsys.readouterr().err
    (tmp_path / "pollingdata_freeze.json").write_text(json.dumps(_pd()), encoding="utf-8")
    assert len(sb.pollingdata("freeze", manual_dir=tmp_path)) == 3


def test_archive_url_flags_accept_both_spellings():
    a = sb.build_parser().parse_args(
        [
            "--label",
            "freeze",
            "--no-fetch",
            "--archive-url-election-winner",
            "https://a",
            "--archive-url-first_place",
            "b",
        ]
    )
    assert (a.archive_url_election_winner, a.archive_url_first_place, a.archive_url_first_round_outright_win) == (
        "https://a",
        "b",
        "",
    )
    assert a.no_fetch and a.events == list(sb.EVENTS)

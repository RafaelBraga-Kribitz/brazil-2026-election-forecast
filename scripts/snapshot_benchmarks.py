"""Snapshot the external 2026 benchmarks at a stated time (PREREG s.7 and s.11).

Polymarket: public Gamma API (no login). Each event response is saved byte-for-byte to data/raw/benchmarks/
(git-ignored, not redistributed) next to a provenance record holding the request URL, retrieval time and SHA-256.
Every market row of every event is kept, including placeholders and names that are not on the ballot.

PollingData: its page is rendered client-side, so the displayed values are read in a browser and typed into
data/manual/pollingdata_<label>.json. This script validates that file and converts displayed shares to valid votes.
It never computes or infers a probability that PollingData did not display.

  {"retrieved_utc": "2026-10-04T01:00:00Z", "url": "...", "archive_url": "",
   "basis": "total_incl_nao_valido",            # or "valid" (values already on a valid-vote basis)
   "values_pct": {"Lula": 40.8, ...}, "nao_valido_pct": 9.4,   # nao_valido_pct required for the total basis
   "institutes_note": "...", "methodology_note": "...", "last_poll_date": "YYYY-MM-DD",
   "screenshot_sha256": "",                     # optional, SHA-256 of a local screenshot of the page
   "forecast": {                                # optional; only if PollingData displays probabilities
     "url": "...", "retrieved_utc": "...", "archive_url": "",
     "win_probability_pct": {"Lula": 55.0, ...},  # only the values displayed; nothing imputed
     "first_round_outright_pct": 12.0,             # or null when not displayed
     "method_note": "..."}}

Usage:
  python scripts/snapshot_benchmarks.py --label freeze
      fetch Polymarket now, then write data/manual/benchmarks_2026_<label>.csv
  python scripts/snapshot_benchmarks.py --label freeze --no-fetch --archive-url-election-winner <url>
      rebuild the CSV from the raw files already saved for <label> (after the PollingData file is typed in)
  --events election_winner                        restrict to some Polymarket events (e.g. at the runoff freeze)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from brfc import config
from brfc.names import _key
from brfc.provenance import SourceRecord, read_record, sha256_bytes, sha256_file, utc_now_iso, write_record

GAMMA = "https://gamma-api.polymarket.com/events"
EVENTS = {
    "first_place": "brazil-presidential-election-first-round-winner",
    "first_round_outright_win": (
        "will-any-presidential-candidate-win-outright-in-the-first-round-of-the-brazil-election"
    ),
    "election_winner": "brazil-presidential-election",
}
EVENT_IDS = {"first_place": "943054", "first_round_outright_win": "45924", "election_winner": "45915"}
CANDIDATE_EVENTS = ("first_place", "election_winner")  # one market per outcome; mapped to a ballot name
ALIASES = {"luiz inacio lula da silva": "Lula", "zema": "Romeu Zema", "samara": "Samara Martins"}
RAW_DIR = config.DATA / "raw" / "benchmarks"
MANUAL_DIR = config.DATA / "manual"

PD_BASES = ("total_incl_nao_valido", "valid")
PD_TOTAL_TOL = 2.0  # pp: displayed values + 'não válido' may exceed 100 by rounding only
PM_NOTE = "market price (not a probability forecast published by a model; not ground truth)"


def _candidate(text: str) -> str | None:
    """Map a market question or displayed name to a 2026 ballot name, accent-insensitively.

    Matches a full ballot name or a known short name as whole words. Returns None when nothing matches (names not
    on the ballot, including the revoked candidacy, placeholders and "another candidate") or when more than one
    ballot name matches (ambiguous)."""
    q = f" {_key(text)} "
    hits = {name for name in config.BALLOTS["2026"] if f" {_key(name)} " in q}
    hits |= {name for alias, name in ALIASES.items() if f" {alias} " in q}
    return hits.pop() if len(hits) == 1 else None


def _float(x) -> float | None:
    return None if x is None or x == "" else float(x)


def _yes_price(market: dict) -> float | None:
    prices = market.get("outcomePrices")
    prices = json.loads(prices) if isinstance(prices, str) else prices
    if not prices:
        return None
    outcomes = market.get("outcomes")
    outcomes = json.loads(outcomes) if isinstance(outcomes, str) else (outcomes or [])
    i = outcomes.index("Yes") if "Yes" in outcomes else 0
    return _float(prices[i])


def parse_polymarket_events(
    json_list: list[dict] | dict, kind: str, slug: str, retrieved_utc: str, raw_sha256: str, *, archive_url: str = ""
) -> list[dict]:
    """One row per market of every event in a Gamma /events response. No filtering, no normalisation."""
    if kind not in EVENTS:
        raise ValueError(f"unknown event kind {kind!r}; expected one of {sorted(EVENTS)}")
    events = [json_list] if isinstance(json_list, dict) else list(json_list)
    rows = []
    for ev in events:
        for m in ev.get("markets") or []:
            question = m.get("question") or ""
            rows.append(
                {
                    "benchmark": "Polymarket",
                    "event_kind": kind,
                    "event_id": str(ev.get("id", "")),
                    "event_slug": slug,
                    "event_title": ev.get("title") or "",
                    "market_id": str(m.get("id", "")),
                    "question": question,
                    "outcome_label": m.get("groupItemTitle") or "",
                    "candidate": _candidate(question) if kind in CANDIDATE_EVENTS else "",
                    "yes_price": _yes_price(m),
                    "best_bid": _float(m.get("bestBid")),
                    "best_ask": _float(m.get("bestAsk")),
                    "volume_usd": float(m.get("volume") or 0.0),
                    "active": m.get("active"),
                    "closed": m.get("closed"),
                    "retrieved_utc": retrieved_utc,
                    "url": f"https://polymarket.com/event/{slug}",
                    "archive_url": archive_url,
                    "raw_sha256": raw_sha256,
                    "note": PM_NOTE,
                }
            )
    return rows


def event_id_problems(rows: list[dict]) -> list[str]:
    """Event ids that differ from the ones recorded in EVENT_IDS (e.g. a slug re-used for a new event)."""
    seen = {(r["event_kind"], r["event_id"]) for r in rows if r.get("benchmark") == "Polymarket"}
    return [
        f"{kind}: event id {eid} (expected {EVENT_IDS[kind]})" for kind, eid in sorted(seen) if eid != EVENT_IDS[kind]
    ]


def _raw_paths(raw_dir: Path, kind: str, label: str) -> tuple[Path, Path]:
    stem = f"polymarket_{EVENTS[kind]}_{label}"
    return raw_dir / f"{stem}.json", raw_dir / f"{stem}.provenance.json"


def _rel(p: Path) -> str:
    try:
        return p.relative_to(config.ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def fetch_polymarket(label: str, kinds: list[str], raw_dir: Path = RAW_DIR) -> None:
    """Network step: save each Gamma response and its provenance record. Falls back to the event id if the
    slug returns nothing."""
    import requests

    from brfc.ingest.wikipedia import USER_AGENT

    raw_dir.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": USER_AGENT}
    for kind in kinds:
        r = requests.get(GAMMA, params={"slug": EVENTS[kind]}, headers=headers, timeout=60)
        r.raise_for_status()
        ts = utc_now_iso()
        if r.json() == []:
            r = requests.get(f"{GAMMA}/{EVENT_IDS[kind]}", headers=headers, timeout=60)
            r.raise_for_status()
            ts = utc_now_iso()
        raw, rec = _raw_paths(raw_dir, kind, label)
        raw.write_bytes(r.content)
        record = SourceRecord(
            source="polymarket_gamma",
            url=r.url,
            retrieval_timestamp=ts,
            source_type="api_response",
            revision=None,
            sha256=sha256_bytes(r.content),
            local_path=_rel(raw),
            description=f"{kind} (event {EVENT_IDS[kind]})",
        )
        write_record(record, rec)
        print(f"fetched {kind} at {ts} sha256 {record.sha256[:12]}")


def load_polymarket(
    label: str, kinds: list[str], archive_urls: dict[str, str] | None = None, raw_dir: Path = RAW_DIR
) -> list[dict]:
    """Offline step: parse the saved responses for `label`, checking each file against its recorded hash."""
    archive_urls = archive_urls or {}
    rows = []
    for kind in kinds:
        raw, rec_path = _raw_paths(raw_dir, kind, label)
        if not (raw.exists() and rec_path.exists()):
            raise FileNotFoundError(f"no saved Polymarket response for {kind!r} / label {label!r} in {raw_dir}")
        rec = read_record(rec_path)
        if sha256_file(raw) != rec.sha256:
            raise ValueError(f"{raw.name} does not match the SHA-256 in its provenance record")
        rows += parse_polymarket_events(
            json.loads(raw.read_bytes()),
            kind,
            EVENTS[kind],
            rec.retrieval_timestamp,
            rec.sha256,
            archive_url=archive_urls.get(kind, ""),
        )
    return rows


def _pct(x, what: str) -> float:
    if isinstance(x, bool) or not isinstance(x, int | float):
        raise ValueError(f"{what}: expected a number, got {x!r}")
    if not 0.0 <= float(x) <= 100.0:
        raise ValueError(f"{what}: {x} is outside [0, 100]")
    return float(x)


def _utc(x, what: str) -> str:
    try:
        datetime.fromisoformat(str(x))
    except ValueError as e:
        raise ValueError(f"{what}: {x!r} is not an ISO-8601 timestamp") from e
    return str(x)


def _require(d: dict, keys: tuple[str, ...], what: str) -> None:
    missing = [k for k in keys if d.get(k) in (None, "")]
    if missing:
        raise ValueError(f"{what}: missing {missing}")


def parse_pollingdata(d: dict) -> list[dict]:
    """Rows for the values PollingData displayed: valid-vote shares, plus displayed probabilities if present."""
    _require(d, ("retrieved_utc", "url", "basis", "values_pct"), "PollingData")
    if d["basis"] not in PD_BASES:
        raise ValueError(f"PollingData basis {d['basis']!r}; expected one of {PD_BASES}")
    shown = {str(c): v for c, v in d["values_pct"].items() if v is not None}  # null = not displayed
    values = {c: _pct(v, f"values_pct[{c!r}]") for c, v in shown.items()}
    if not values:
        raise ValueError("PollingData values_pct is empty")
    total = sum(values.values())
    if d["basis"] == "total_incl_nao_valido":
        if d.get("nao_valido_pct") is None:
            raise ValueError("basis total_incl_nao_valido needs nao_valido_pct (as displayed)")
        nv = _pct(d["nao_valido_pct"], "nao_valido_pct")
        if nv >= 100.0 or total + nv > 100.0 + PD_TOTAL_TOL:
            raise ValueError(f"displayed values ({total:.1f}) + nao_valido ({nv:.1f}) exceed 100")
        denom = 100.0 - nv
        rule = "valid share = displayed / (100 - 'não válido'); candidates not displayed are not imputed"
    else:
        nv = None
        if total > 100.0 + PD_TOTAL_TOL:
            raise ValueError(f"valid-basis values sum to {total:.1f}")
        # renormalise rounding drift only; a partial list is kept as displayed (no mass is moved to shown names)
        denom = total if abs(total - 100.0) <= PD_TOTAL_TOL else 100.0
        rule = "valid share = displayed (rescaled to 100 only for rounding); candidates not displayed are not imputed"
    retrieved = _utc(d["retrieved_utc"], "retrieved_utc")
    common = {"benchmark": "PollingData", "url": d["url"], "archive_url": d.get("archive_url", "")}
    rows = [
        common
        | {
            "event_kind": "share",
            "candidate": _candidate(c),
            "candidate_displayed": c,
            "value_displayed_pct": v,
            "basis_displayed": d["basis"],
            "nao_valido_pct": nv,
            "valid_share_pct": 100.0 * v / denom,
            "retrieved_utc": retrieved,
            "last_poll_date": d.get("last_poll_date"),
            "institutes_note": d.get("institutes_note", ""),
            "methodology_note": d.get("methodology_note", ""),
            "screenshot_sha256": d.get("screenshot_sha256", ""),
            "note": rule,
        }
        for c, v in values.items()
    ]

    f = d.get("forecast")
    if not f:
        return rows
    _require(f, ("retrieved_utc", "url"), "PollingData forecast")
    fc = {
        "benchmark": "PollingData",
        "event_kind": "pollingdata_win_probability",
        "retrieved_utc": _utc(f["retrieved_utc"], "forecast.retrieved_utc"),
        "url": f["url"],
        "archive_url": f.get("archive_url", ""),
        "methodology_note": f.get("method_note", ""),
        "note": "probability as displayed by PollingData; nothing computed or imputed here; not ground truth",
    }
    shown = {str(c): v for c, v in (f.get("win_probability_pct") or {}).items() if v is not None}
    win = {c: _pct(v, f"win_probability_pct[{c!r}]") for c, v in shown.items()}
    if sum(win.values()) > 100.0 + PD_TOTAL_TOL:
        raise ValueError(f"displayed win probabilities sum to {sum(win.values()):.1f}")
    rows += [
        fc
        | {
            "probability_event": "election_winner",
            "candidate": _candidate(c),
            "candidate_displayed": c,
            "value_displayed_pct": v,
        }
        for c, v in win.items()
    ]
    if f.get("first_round_outright_pct") is not None:
        rows.append(
            fc
            | {
                "probability_event": "first_round_outright_win",
                "candidate": "",
                "candidate_displayed": "",
                "value_displayed_pct": _pct(f["first_round_outright_pct"], "first_round_outright_pct"),
            }
        )
    return rows


def pollingdata(label: str, manual_dir: Path = MANUAL_DIR) -> list[dict]:
    p = manual_dir / f"pollingdata_{label}.json"
    if not p.exists():
        print(f"WARNING: {_rel(p)} not found; PollingData (baseline A) is not in this snapshot", file=sys.stderr)
        return []
    rows = parse_pollingdata(json.loads(p.read_text(encoding="utf-8")))
    unmapped = sorted({r["candidate_displayed"] for r in rows if r["candidate"] is None})
    if unmapped:
        print(f"note: PollingData names not matched to a ballot name: {unmapped}", file=sys.stderr)
    return rows


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--label", required=True, help="e.g. freeze, runoff_freeze")
    ap.add_argument("--events", nargs="+", choices=list(EVENTS), default=list(EVENTS), help="Polymarket events")
    ap.add_argument("--no-fetch", action="store_true", help="re-parse the raw files already saved for --label")
    for kind in EVENTS:
        ap.add_argument(
            f"--archive-url-{kind.replace('_', '-')}",
            f"--archive-url-{kind}",
            dest=f"archive_url_{kind}",
            default="",
            help=f"archive URL of the Polymarket {kind} page",
        )
    return ap


def main(argv: list[str] | None = None) -> None:
    a = build_parser().parse_args(argv)
    if not a.no_fetch:
        fetch_polymarket(a.label, a.events)
    archive = {k: getattr(a, f"archive_url_{k}") for k in EVENTS}
    pm = load_polymarket(a.label, a.events, archive)
    for problem in event_id_problems(pm):
        print(f"WARNING: {problem}; check the event before using this snapshot", file=sys.stderr)
    pdata = pollingdata(a.label)
    out = pd.DataFrame(pm + pdata)
    path = MANUAL_DIR / f"benchmarks_2026_{a.label}.csv"
    out.to_csv(path, index=False)
    with pd.option_context("display.width", 200, "display.max_colwidth", 40):
        if pm:
            traded = pd.DataFrame(pm)
            traded = traded[traded["volume_usd"] > 0]
            print(traded[["event_kind", "market_id", "candidate", "yes_price", "volume_usd"]].to_string(index=False))
        if pdata:
            cols = ["event_kind", "candidate_displayed", "value_displayed_pct", "valid_share_pct"]
            print(pd.DataFrame(pdata).reindex(columns=cols).to_string(index=False))
    print(f"wrote {_rel(path)} ({len(pm)} Polymarket rows, {len(pdata)} PollingData rows)")


if __name__ == "__main__":
    main()

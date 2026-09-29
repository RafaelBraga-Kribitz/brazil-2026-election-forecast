"""Snapshot the external 2026 benchmarks at a stated time (PREREG s.7 and s.11).

Polymarket: public Gamma API (no login). Raw JSON goes to data/raw/benchmarks/ (not redistributed) and its SHA-256
is recorded. PollingData: its page is rendered client-side, so the displayed values are read in a browser and typed
into data/manual/pollingdata_<label>.json (fields documented below); this script validates that file and converts it
to valid votes.

  {"retrieved_utc": "...", "url": "...", "basis": "total_incl_nao_valido",
   "values_pct": {"Lula": 40.8, ...}, "nao_valido_pct": 9.4,
   "institutes_note": "...", "methodology_note": "...", "last_poll_date": "YYYY-MM-DD"}

Usage: python scripts/snapshot_benchmarks.py --label freeze   (writes data/manual/benchmarks_2026_<label>.csv)
"""

from __future__ import annotations

import argparse
import json

import pandas as pd
import requests

from brfc import config
from brfc.ingest.wikipedia import USER_AGENT
from brfc.provenance import sha256_bytes, utc_now_iso

GAMMA = "https://gamma-api.polymarket.com/events"
EVENTS = {
    "first_place": "brazil-presidential-election-first-round-winner",
    "first_round_outright_win": (
        "will-any-presidential-candidate-win-outright-in-the-first-round-of-the-brazil-election"
    ),
    "election_winner": "brazil-presidential-election",
}
ALIASES = {"luiz inacio lula da silva": "Lula", "zema": "Romeu Zema", "samara": "Samara Martins"}


def _candidate(question: str) -> str | None:
    """Map a market question to a ballot name (accent-insensitive; full name, else a known short name)."""
    from brfc.names import _key

    q = f" {_key(question)} "
    for ballot_name in config.BALLOTS["2026"]:
        if f" {_key(ballot_name)} " in q:
            return ballot_name
    for alias, name in ALIASES.items():
        if f" {alias} " in q:
            return name
    return None


def polymarket(label: str) -> list[dict]:
    raw_dir = config.DATA / "raw" / "benchmarks"
    raw_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for kind, slug in EVENTS.items():
        r = requests.get(GAMMA, params={"slug": slug}, headers={"User-Agent": USER_AGENT}, timeout=60)
        r.raise_for_status()
        ts = utc_now_iso()
        blob = r.content
        (raw_dir / f"polymarket_{slug}_{label}.json").write_bytes(blob)
        for ev in r.json():
            for m in ev.get("markets", []):
                prices = json.loads(m.get("outcomePrices") or "null") or [None]
                rows.append(
                    {
                        "benchmark": "Polymarket",
                        "event_kind": kind,
                        "event_id": ev.get("id"),
                        "event_slug": slug,
                        "market_id": m.get("id"),
                        "question": m.get("question"),
                        "candidate": _candidate(m.get("question") or "") if kind != "first_round_outright_win" else "",
                        "yes_price": float(prices[0]) if prices[0] is not None else None,
                        "best_bid": m.get("bestBid"),
                        "best_ask": m.get("bestAsk"),
                        "volume_usd": float(m.get("volume") or 0),
                        "closed": m.get("closed"),
                        "retrieved_utc": ts,
                        "url": f"https://polymarket.com/event/{slug}",
                        "raw_sha256": sha256_bytes(blob),
                        "note": "market price (not a probability forecast published by a model; not ground truth)",
                    }
                )
    return rows


def pollingdata(label: str) -> list[dict]:
    p = config.DATA / "manual" / f"pollingdata_{label}.json"
    if not p.exists():
        return []
    d = json.loads(p.read_text(encoding="utf-8"))
    nv = float(d.get("nao_valido_pct") or 0.0)
    total = sum(d["values_pct"].values())
    rows = []
    for cand, v in d["values_pct"].items():
        valid = 100.0 * v / (100.0 - nv) if d["basis"] == "total_incl_nao_valido" else 100.0 * v / total
        rows.append(
            {
                "benchmark": "PollingData",
                "event_kind": "share",
                "candidate": cand,
                "value_displayed_pct": v,
                "basis_displayed": d["basis"],
                "nao_valido_pct": nv,
                "valid_share_pct": valid,
                "retrieved_utc": d["retrieved_utc"],
                "url": d["url"],
                "last_poll_date": d.get("last_poll_date"),
                "institutes_note": d.get("institutes_note", ""),
                "methodology_note": d.get("methodology_note", ""),
                "note": "valid share = displayed / (100 - 'não válido'); candidates not displayed are not imputed",
            }
        )
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    a = ap.parse_args()
    out = pd.DataFrame(polymarket(a.label) + pollingdata(a.label))
    path = config.DATA / "manual" / f"benchmarks_2026_{a.label}.csv"
    out.to_csv(path, index=False)
    pm = out[(out["benchmark"] == "Polymarket") & (out["volume_usd"] > 0)]
    print(pm[["event_kind", "market_id", "candidate", "yes_price", "volume_usd"]].to_string(index=False))
    if (out["benchmark"] == "PollingData").any():
        print(out.loc[out["benchmark"] == "PollingData", ["candidate", "value_displayed_pct", "valid_share_pct"]])
    print(f"wrote {path.relative_to(config.ROOT)}")


if __name__ == "__main__":
    main()

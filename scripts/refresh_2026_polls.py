"""Re-parse the 2026 poll tables from the Wikipedia PT/EN revisions current at (or before) a timestamp.

Pinning to "latest revision at or before the freeze" means no poll published after the freeze can enter.
Usage: python scripts/refresh_2026_polls.py --at 2026-10-04T01:00:00Z
Updates data/interim/polls_wiki_2026.csv, conflicts_wiki_2026.csv and data/SOURCES.lock.json.
"""

from __future__ import annotations

import argparse
import json

import pandas as pd

from brfc import config
from brfc.ingest import wiki_2026
from brfc.ingest.wikipedia import RAW_DIR, revision_at
from brfc.provenance import read_record, utc_now_iso


def update_lock() -> None:
    used = set()
    for f in sorted((config.DATA / "interim").glob("polls_wiki_*.csv")):
        d = pd.read_csv(f, dtype=str)
        used |= set(zip(d.source_url, d.source_revision, d.source_hash, strict=True))
    recs = {}
    for p in RAW_DIR.glob("*.provenance.json"):
        r = read_record(p)
        recs[r.sha256] = r
    lock = [
        {
            "source_url": u,
            "revision": rev,
            "sha256": h,
            "retrieval_timestamp": recs[h].retrieval_timestamp if h in recs else None,
            "title": recs[h].description if h in recs else None,
            "raw_cached_locally": h in recs,
        }
        for u, rev, h in sorted(used)
    ]
    (config.DATA / "SOURCES.lock.json").write_text(
        json.dumps(lock, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", default=utc_now_iso(), help="UTC timestamp; latest revision at or before it is used")
    a = ap.parse_args()
    pt, pt_ts = revision_at("pt", wiki_2026.PT_TITLE, a.at)
    en, en_ts = revision_at("en", wiki_2026.EN_TITLE, a.at)
    print(f"PT oldid {pt} ({pt_ts}); EN oldid {en} ({en_ts}); at/before {a.at}")
    wiki_2026.main(pt_oldid=pt, en_oldid=en)
    update_lock()
    meta = {
        "requested_at_utc": a.at,
        "pt_oldid": pt,
        "pt_revision_timestamp": pt_ts,
        "en_oldid": en,
        "en_revision_timestamp": en_ts,
        "refreshed_utc": utc_now_iso(),
    }
    (config.DATA / "interim" / "polls_wiki_2026.revision.json").write_text(
        json.dumps(meta, indent=1) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()

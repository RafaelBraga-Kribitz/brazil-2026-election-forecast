"""Reproducibility check of the transformed datasets: re-parse every pinned Wikipedia revision and compare.

1. Verifies each locked revision's stable identity (MediaWiki wikitext SHA-1) against the API.
2. Re-parses the pinned revisions with the committed parsers (fetching the HTML if it is not cached), compares every
   value of every table with the committed version, then restores the committed files byte for byte.

Columns that describe the retrieval rather than the data are excluded from the comparison: `retrieval_timestamp`
and `source_hash` (the SHA-256 of the rendered HTML, which MediaWiki can re-render with different bytes for the same
revision). Exit code 1 on any difference. The committed tables stay the pipeline's input.
"""

from __future__ import annotations

import json
import sys

import pandas as pd

from brfc import config
from brfc.ingest import wiki_2014, wiki_2018, wiki_2022, wiki_2026
from brfc.ingest.wikipedia import revision_sha1

TABLES = [
    "polls_wiki_2014",
    "polls_wiki_2018",
    "polls_wiki_2022",
    "polls_wiki_2026",
    "polls_wiki_2026_runoff",
    "conflicts_wiki_2026",
    "conflicts_wiki_2026_runoff",
]
RETRIEVAL_COLUMNS = {"retrieval_timestamp", "source_hash"}


def check_revision_identity() -> list[str]:
    problems = []
    for x in json.loads((config.DATA / "SOURCES.lock.json").read_text(encoding="utf-8")):
        lang = "pt" if "//pt." in x["source_url"] else "en"
        live = revision_sha1(lang, int(x["revision"]))["revision_sha1"]
        if x.get("revision_sha1") != live:
            problems.append(f"revision {x['revision']}: locked sha1 {x.get('revision_sha1')} != API {live}")
    return problems


def reparse() -> None:
    wiki_2014.main()
    wiki_2018.main()
    wiki_2022.main()
    rev = json.loads((config.DATA / "interim" / "polls_wiki_2026.revision.json").read_text(encoding="utf-8"))
    wiki_2026.main(pt_oldid=int(rev["pt_oldid"]), en_oldid=int(rev["en_oldid"]))


def main() -> int:
    paths = {t: config.DATA / "interim" / f"{t}.csv" for t in TABLES}
    committed = {t: p.read_bytes() for t, p in paths.items() if p.exists()}
    problems = check_revision_identity()
    try:
        reparse()
        for t, raw in committed.items():
            old = pd.read_csv(pd.io.common.BytesIO(raw), dtype=str).fillna("")
            new = pd.read_csv(paths[t], dtype=str).fillna("")
            cols = [c for c in old.columns if c not in RETRIEVAL_COLUMNS]
            if list(old.columns) != list(new.columns) or old.shape != new.shape:
                problems.append(f"{t}: shape/columns differ {old.shape} vs {new.shape}")
                continue
            diff = [c for c in cols if not old[c].equals(new[c])]
            if diff:
                problems.append(f"{t}: values differ in {diff}")
            else:
                print(f"{t}: {len(old)} rows, all {len(cols)} data columns identical")
    finally:
        for t, raw in committed.items():
            paths[t].write_bytes(raw)
    for p in problems:
        print("PROBLEM:", p)
    print("reparse check:", "OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

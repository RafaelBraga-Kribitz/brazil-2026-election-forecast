"""Data-readiness gate. Machine-checkable; safe to run at any time.
Writes only outputs/readiness.json and outputs/tse_reconciliation_*.csv.

REQUIRED  (blocks the backtest/forecast if missing or invalid)
  - parsed Wikipedia poll tables data/interim/polls_wiki_{2014,2018,2022,2026}.csv and
    polls_wiki_2026_runoff.csv: canonical schema + provenance
  - data/SOURCES.lock.json covering every source revision/hash used by those tables
  - non-Wikipedia poll tables (canonical schema; no revision; source_hash = SHA-256 of the cited artefact, with
    LF or CRLF line endings):
      data/interim/polls_releases.csv -> data/manual/final_poll_verification.csv (Addendum 02)
      data/interim/polls_2014_supplement.csv -> the data/manual/research_2014/<file>.csv holding each poll
      (Addendum 04)
  - data/manual/results_secondary.csv: vote counts sum to the valid total for all 6 historical rounds
  - data/manual/ballot_2026.csv
RECONCILIATION (does not block; upgrades verification status when present)
  - data/raw/tse/votacao_candidato_munzona_{2014,2018,2022}.zip  (manual browser download; SHA-256 recorded)
OPTIONAL
  - data/raw/tse/pesquisas_eleitorais_2026*.zip|csv (PesqEle registration metadata: BR-ID, n, dates)

Exit code 0 when all REQUIRED checks pass.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

from brfc import config
from brfc.ingest import tse
from brfc.names import canonical_pollster
from brfc.provenance import sha256_file, utc_now_iso
from brfc.schema import validate_polls

KNOWN_SOURCE_ISSUES = ("total-basis poll-scenarios summing above 101.5%",)
INTERIM = config.DATA / "interim"
WIKI_TABLES = [*(f"polls_wiki_{e}.csv" for e in ("2014", "2018", "2022", "2026")), "polls_wiki_2026_runoff.csv"]
RELEASES_ARTEFACT = config.DATA / "manual" / "final_poll_verification.csv"
RESEARCH_2014 = config.DATA / "manual" / "research_2014"
RESEARCH_NOT_INPUTS = {"verification.csv", "supplement_exclusions.csv"}


def _schema_problems(d: pd.DataFrame) -> list[str]:
    return [x for x in validate_polls(d) if not x.startswith(KNOWN_SOURCE_ISSUES)]


def text_sha256(path: Path) -> set[str]:
    """SHA-256 of a text artefact with LF and with CRLF line endings (git stores LF; a working copy may use CRLF)."""
    lf = path.read_bytes().replace(b"\r\n", b"\n")
    return {hashlib.sha256(lf).hexdigest(), hashlib.sha256(lf.replace(b"\n", b"\r\n")).hexdigest()}


def _poll_key(x) -> tuple:
    return (int(x.round), canonical_pollster(x.pollster), x.field_start, x.field_end, int(float(x.sample_size)))


def _artefact_problems(name: str, d: pd.DataFrame) -> list[str]:
    """Source-hash rule of a non-Wikipedia table: no revision; source_hash = SHA-256 of the artefact it was built
    from (the verification file for release rows; the research file holding the poll for the 2014 supplement)."""
    probs = []
    if d["source_revision"].fillna("").astype(str).str.strip().ne("").any():
        probs.append("rows cite a Wikipedia revision")
    if name == "polls_releases.csv":
        bad = sorted(set(d["source_hash"]) - text_sha256(RELEASES_ARTEFACT))
        if bad:
            probs.append(f"source_hash is not the SHA-256 of {RELEASES_ARTEFACT.name}: {bad}")
        return probs
    held: dict[tuple, set[str]] = {}
    for f in sorted(p for p in RESEARCH_2014.glob("*.csv") if p.name not in RESEARCH_NOT_INPUTS):
        h = text_sha256(f)
        for x in pd.read_csv(f, dtype=str).dropna(subset=["sample_size"]).itertuples(index=False):
            held.setdefault(_poll_key(x), set()).update(h)
    bad = sorted(
        x.poll_id
        for x in d.drop_duplicates(["poll_id", "source_hash"]).itertuples(index=False)
        if x.source_hash not in held.get(_poll_key(x), set())
    )
    if bad:
        probs.append(f"source_hash is not the SHA-256 of the research_2014 file holding the poll: {bad}")
    return probs


def poll_table_checks(lock: list[dict]) -> dict[str, str]:
    """REQUIRED poll-table checks (read-only): 'OK', 'MISSING' or 'INVALID: ...' per table."""
    out = {}
    locked = {(x["revision"], x["sha256"]) for x in lock}
    for name in WIKI_TABLES:
        p = INTERIM / name
        if not p.exists():
            out[name] = "MISSING"
            continue
        d = pd.read_csv(p, dtype={"election": str, "source_revision": str})
        probs = _schema_problems(d)
        unlocked = set(zip(d["source_revision"], d["source_hash"], strict=True)) - locked
        out[name] = "OK" if not probs and not unlocked else f"INVALID: {probs} unlocked={sorted(unlocked)}"
    for name in ("polls_releases.csv", "polls_2014_supplement.csv"):
        p = INTERIM / name
        if not p.exists():
            out[name] = "MISSING"
            continue
        d = pd.read_csv(p, dtype={"election": str, "source_revision": str})
        probs = _schema_problems(d) + _artefact_problems(name, d)
        out[name] = "OK" if not probs else f"INVALID: {probs}"
    return out


def main() -> int:
    report = {"checked_utc": utc_now_iso(), "required": {}, "reconciliation": {}, "optional": {}}
    lock = json.loads((config.DATA / "SOURCES.lock.json").read_text(encoding="utf-8"))
    report["required"].update(poll_table_checks(lock))
    ok = all(v == "OK" for v in report["required"].values())
    r = pd.read_csv(config.DATA / "manual" / "results_secondary.csv", dtype={"election": str})
    tot = r.groupby(["election", "round"]).agg(v=("votes", "sum"), t=("total_valid_votes", "first"))
    good = len(tot) == 6 and bool((tot["v"] == tot["t"]).all())
    report["required"]["results_secondary.csv"] = "OK" if good else "INVALID totals"
    ok &= good
    b = config.DATA / "manual" / "ballot_2026.csv"
    report["required"]["ballot_2026.csv"] = "OK" if b.exists() else "MISSING"
    ok &= b.exists()

    for e in config.HISTORICAL:
        f = tse.expected_file(e)
        if not f.exists():
            report["reconciliation"][f.name] = "PENDING manual download"
            continue
        rec = tse.reconcile(e, r)
        out = config.OUTPUTS / f"tse_reconciliation_{e}.csv"
        config.OUTPUTS.mkdir(exist_ok=True)
        rec.to_csv(out, index=False)
        mism = rec[(rec["difference"].fillna(1) != 0)]
        report["reconciliation"][f.name] = {"sha256": sha256_file(f), "rows": len(rec), "mismatches": len(mism)}
    pes = (
        sorted((config.DATA / "raw" / "tse").glob("pesquisa*_eleitora*_2026*"))
        if (config.DATA / "raw" / "tse").exists()
        else []
    )
    report["optional"]["pesqele_2026"] = [{"file": p.name, "sha256": sha256_file(p)} for p in pes] or "not provided"
    report["ready"] = ok
    config.OUTPUTS.mkdir(exist_ok=True)
    (config.OUTPUTS / "readiness.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

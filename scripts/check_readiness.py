"""Data-readiness gate. Machine-checkable; safe to run at any time (read-only except outputs/readiness.json).

REQUIRED  (blocks the backtest/forecast if missing or invalid)
  - parsed poll tables data/interim/polls_wiki_{2014,2018,2022,2026}.csv: canonical schema + provenance
  - data/SOURCES.lock.json covering every source revision/hash used by those tables
  - data/manual/results_secondary.csv: vote counts sum to the valid total for all 6 historical rounds
  - data/manual/ballot_2026.csv
RECONCILIATION (does not block; upgrades verification status when present)
  - data/raw/tse/votacao_candidato_munzona_{2014,2018,2022}.zip  (manual browser download; SHA-256 recorded)
OPTIONAL
  - data/raw/tse/pesquisas_eleitorais_2026*.zip|csv (PesqEle registration metadata: BR-ID, n, dates)

Exit code 0 when all REQUIRED checks pass.
"""

from __future__ import annotations

import json
import sys

import pandas as pd

from brfc import config
from brfc.ingest import tse
from brfc.provenance import sha256_file, utc_now_iso
from brfc.schema import validate_polls

KNOWN_SOURCE_ISSUES = ("total-basis poll-scenarios summing above 101.5%",)


def main() -> int:
    report = {"checked_utc": utc_now_iso(), "required": {}, "reconciliation": {}, "optional": {}}
    ok = True
    lock = json.loads((config.DATA / "SOURCES.lock.json").read_text(encoding="utf-8"))
    locked = {(x["revision"], x["sha256"]) for x in lock}
    for e in ("2014", "2018", "2022", "2026"):
        p = config.DATA / "interim" / f"polls_wiki_{e}.csv"
        if not p.exists():
            report["required"][p.name] = "MISSING"
            ok = False
            continue
        d = pd.read_csv(p, dtype={"election": str, "source_revision": str})
        probs = [x for x in validate_polls(d) if not x.startswith(KNOWN_SOURCE_ISSUES)]
        unlocked = set(zip(d["source_revision"], d["source_hash"], strict=True)) - locked
        status = "OK" if not probs and not unlocked else f"INVALID: {probs} unlocked={sorted(unlocked)}"
        report["required"][p.name] = status
        ok &= status == "OK"
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

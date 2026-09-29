"""Reconcile the secondary-source national results against TSE open-data files downloaded manually.

Expected files (browser download; TSE endpoints return 403 to scripts, which this project does not bypass):
    data/raw/tse/votacao_candidato_munzona_{2014,2018,2022}.zip
from https://dadosabertos.tse.jus.br/ ("Resultados {year}" -> "Votação nominal por município e zona").
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from pathlib import Path

import pandas as pd

from brfc import config

TSE_DIR = config.DATA / "raw" / "tse"


def expected_file(election: str) -> Path:
    return TSE_DIR / f"votacao_candidato_munzona_{election}.zip"


def _norm(s: str) -> set[str]:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return {t for t in re.split(r"[^a-z]+", s) if len(t) > 2}


def national_totals(zip_path: Path) -> pd.DataFrame:
    """Sum presidential nominal votes by round and ballot name.

    Uses the _BR file if present, to avoid double counting."""
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        br = [n for n in names if re.search(r"_BR\.csv$", n, re.I)]
        use = br or [n for n in names if not re.search(r"_BRASIL\.csv$", n, re.I)]
        frames = []
        for n in use:
            with z.open(n) as fh:
                d = pd.read_csv(
                    io.TextIOWrapper(fh, encoding="latin-1"),
                    sep=";",
                    dtype=str,
                    usecols=lambda c: (
                        c
                        in {
                            "DS_CARGO",
                            "CD_CARGO",
                            "NR_TURNO",
                            "NM_URNA_CANDIDATO",
                            "NR_CANDIDATO",
                            "QT_VOTOS_NOMINAIS",
                        }
                    ),
                )
            cargo = d["DS_CARGO"].str.upper().str.contains("PRESIDENTE") & ~d["DS_CARGO"].str.upper().str.contains(
                "VICE"
            )
            frames.append(d[cargo])
    d = pd.concat(frames)
    d["votes"] = pd.to_numeric(d["QT_VOTOS_NOMINAIS"], errors="coerce").fillna(0).astype(int)
    return d.groupby(["NR_TURNO", "NR_CANDIDATO", "NM_URNA_CANDIDATO"], as_index=False)["votes"].sum()


def reconcile(election: str, secondary: pd.DataFrame) -> pd.DataFrame:
    """Row per secondary-source candidate with the matching TSE vote count and the difference."""
    tse = national_totals(expected_file(election))
    rows = []
    for rnd in (1, 2):
        sec = secondary[(secondary["election"] == election) & (secondary["round"] == rnd)]
        t = tse[tse["NR_TURNO"].astype(int) == rnd]
        for c in sec.itertuples():
            match = t[t["NM_URNA_CANDIDATO"].map(lambda n, c=c: bool(_norm(n) & _norm(c.candidate)))]
            tv = int(match["votes"].sum()) if len(match) == 1 else None
            rows.append(
                {
                    "election": election,
                    "round": rnd,
                    "candidate": c.candidate,
                    "secondary_votes": int(c.votes),
                    "tse_votes": tv,
                    "tse_name": ";".join(match["NM_URNA_CANDIDATO"]),
                    "difference": None if tv is None else int(c.votes) - tv,
                }
            )
    return pd.DataFrame(rows)

"""Canonical pollster and candidate names (aliases collected from the parsed sources)."""

from __future__ import annotations

import re
import unicodedata

POLLSTER_ALIASES: dict[str, str] = {
    "atlas": "AtlasIntel",
    "atlasintel": "AtlasIntel",
    "atlas intel": "AtlasIntel",
    "atlas politico": "AtlasIntel",
    "datapoder360": "PoderData",
    "poderdata": "PoderData",
    "fsb": "FSB",
    "fsb pesquisa": "FSB",
    "ibope": "Ibope",
    "ideia": "Ideia",
    "ideia big data": "Ideia",
    "ipec": "Ipec",
    "instituto verita": "Veritá",
    "verita": "Veritá",
    "realtime big data": "Real Time Big Data",
    "real time big data": "Real Time Big Data",
    "parana pesquisas": "Paraná Pesquisas",
    "quaest": "Quaest",
    "genial quaest": "Quaest",
    "pesquisa365": "Pesquisa365",
}
CANDIDATE_ALIASES: dict[str, str] = {
    "luiz inacio lula da silva": "Lula",
    "lula": "Lula",
}


def _key(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def canonical_pollster(name: str) -> str:
    name = re.sub(r"\[[^\]]*\]", "", str(name)).strip()
    return POLLSTER_ALIASES.get(_key(name), name)


def canonical_candidate(name: str) -> str:
    name = re.sub(r"\[[^\]]*\]", "", str(name)).strip()
    return CANDIDATE_ALIASES.get(_key(name), name)

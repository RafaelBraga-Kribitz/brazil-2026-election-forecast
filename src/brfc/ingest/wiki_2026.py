"""Parse 2026 national stimulated presidential poll tables (first round and runoff head-to-head) from
pinned Wikipedia revisions.

Wikipedia PT ("Pesquisas de opinião para a eleição presidencial no Brasil em 2026") is the primary
source; Wikipedia EN ("Opinion polling for the 2026 Brazilian presidential election") is used as a
gap-filler and conflict check. The parser is driven by page *structure* (section headings, header
cells, row/col spans, footnotes) rather than by specific rows, so it can be re-run on later revisions:

* round 1 reads only tables inside the first-round h2 section ("Primeiro turno" / "First round");
  round 2 reads only tables inside the runoff h2 section ("Segundo turno" / "Second round");
  aggregator tables (no sample-size column) and tables whose heading context mentions spontaneous,
  rejection or state polls (and, for round 1, runoff polls) are ignored;
* the year of each table comes from the enclosing year heading (h3 or collapsible "hidden-title");
* columns are classified from header text (pollster, dates, sample, others, blank/undecided, ...);
  candidate columns are named from the first person link in their header cell;
* rows sharing one rowspanned pollster cell (or one poll_id) are scenarios of the same poll;
* an "Outros"/"Others" cell whose footnote names a single person (or splits the value by person) is
  attributed to that person; otherwise it becomes ``__others__``;
* cells like "<1%" are not numeric: the candidate is kept out of the numeric rows and recorded in
  ``notes`` (never imputed); "-", "—", "N/a" mean the candidate was not in that scenario.

Runoff (round 2) head-to-head tables: PT has one table per pairing, EN one wide table with one row per
pairing. Every scenario must hold exactly two named candidates with numeric shares (anything else is
skipped and counted); its label is "<A> vs <B>" with the two canonical names in ``sorted`` order, so one
pairing has the same label in both languages and across polls. PT/EN scenarios of a matched poll are
paired by that label. Because each pairing is a separate question, a pairing that EN lists for a matched
poll and PT does not is gap-filled from EN at scenario level (PT poll metadata, EN values and
provenance, note ``gap_fill_en_scenario``); whole EN-only polls are gap-filled as in round 1.

Usage: ``python -m brfc.ingest.wiki_2026`` (writes data/interim/polls_wiki_2026.csv,
data/interim/conflicts_wiki_2026.csv, data/interim/polls_wiki_2026_runoff.csv and
data/interim/conflicts_wiki_2026_runoff.csv).
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import pandas as pd
from lxml import html as lxml_html

from brfc.ingest.wikipedia import fetch_revision
from brfc.provenance import SourceRecord
from brfc.schema import CANONICAL_COLUMNS, OTHERS, make_poll_id, validate_polls

PT_TITLE = "Pesquisas de opinião para a eleição presidencial no Brasil em 2026"
EN_TITLE = "Opinion polling for the 2026 Brazilian presidential election"
PT_OLDID = 73082572
EN_OLDID = 1377453715

ELECTION = "2026"
ROUND = 1
MIN_FIELD_END = "2026-06-01"
MATCH_SLACK_DAYS = 7  # parse a little earlier than MIN_FIELD_END so boundary polls can still be matched
SHARE_TOL = 0.05  # pp; PT/EN differences above this are conflicts

ROOT = Path(__file__).resolve().parents[3]
OUT_POLLS = ROOT / "data" / "interim" / "polls_wiki_2026.csv"
OUT_CONFLICTS = ROOT / "data" / "interim" / "conflicts_wiki_2026.csv"
OUT_RUNOFF_POLLS = ROOT / "data" / "interim" / "polls_wiki_2026_runoff.csv"
OUT_RUNOFF_CONFLICTS = ROOT / "data" / "interim" / "conflicts_wiki_2026_runoff.csv"

CONFLICT_COLUMNS = [
    "poll_id",
    "pollster",
    "field_end",
    "candidate",
    "pt_value",
    "en_value",
    "pt_revision",
    "en_revision",
    # kind: share | blank_null | undecided | others_aggregate | field_start | field_end | sample_size
    #       | gap_fill_overlaps_pt_poll
    "kind",
    "pt_scenario",
    "en_scenario",
    "en_poll_id",
    "match_tier",
]

# --------------------------------------------------------------------------------------------------
# text helpers


def _fold(s: str) -> str:
    """Lower-case, accent-free, whitespace-collapsed key."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s).strip().lower()


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _fold(s))


_SKIP_TAGS = {"style", "script", "link", "meta"}
_SKIP_CLASSES = {"sr-only", "sortkey", "mw-editsection", "noprint"}


def _is_hidden(e) -> bool:
    if e.tag in _SKIP_TAGS:
        return True
    cls = set((e.get("class") or "").split())
    if e.tag == "sup" and "reference" in cls:
        return True
    if cls & _SKIP_CLASSES:
        return True
    style = (e.get("style") or "").replace(" ", "").lower()
    return "display:none" in style


def _text(el) -> str:
    """Visible text of a cell: footnote markers, hidden sort keys and screen-reader text removed."""
    parts: list[str] = [el.text or ""]

    def walk(e) -> None:
        if isinstance(e.tag, str) and not _is_hidden(e):
            parts.append(e.text or "")
            for c in e:
                walk(c)
            if e.tag == "br":
                parts.append(" ")
        if e.tail:
            parts.append(e.tail)

    for c in el:
        walk(c)
    return re.sub(r"[\s\u00a0\u202f\u2009]+", " ", "".join(parts)).strip()


def _footnote_refs(el) -> list[tuple[str, str]]:
    """(label, target id) for every footnote/citation marker inside `el`."""
    out = []
    for sup in el.iter("sup"):
        if "reference" not in (sup.get("class") or ""):
            continue
        for a in sup.iter("a"):
            href = a.get("href") or ""
            if href.startswith("#"):
                out.append((re.sub(r"\s+", " ", a.text_content()).strip(), href[1:]))
    return out


def _is_note_label(label: str) -> bool:
    """Explanatory note ([c], [nota 1]) as opposed to a numbered source citation ([20])."""
    return not re.fullmatch(r"\[\s*\d+\s*\]", label)


def _first_person_link(el) -> str | None:
    for a in el.iter("a"):
        hidden = _is_hidden(a)
        for anc in a.iterancestors():
            if anc is el or hidden:
                break
            hidden = _is_hidden(anc)
        if hidden:
            continue
        title = a.get("title") or ""
        if not title or ":" in title.split(" ")[0]:  # skip File:, Ficheiro:, Help: ...
            continue
        return re.sub(r"\s*\((page does not exist|página não existe)\)\s*$", "", title)
    return None


# --------------------------------------------------------------------------------------------------
# canonical names

_CANDIDATE_ALIASES: dict[str, str] = {
    "luiz inacio lula da silva": "Lula",
    "lula": "Lula",
    "flavio bolsonaro": "Flávio Bolsonaro",
    "flavio": "Flávio Bolsonaro",
    "f. bolsonaro": "Flávio Bolsonaro",
    "ronaldo caiado": "Ronaldo Caiado",
    "caiado": "Ronaldo Caiado",
    "romeu zema": "Romeu Zema",
    "zema": "Romeu Zema",
    "renan santos": "Renan Santos",
    "renan": "Renan Santos",
    "santos": "Renan Santos",
    "augusto cury": "Augusto Cury",
    "cury": "Augusto Cury",
    "pablo marcal": "Pablo Marçal",
    "marcal": "Pablo Marçal",
    "samara martins": "Samara Martins",
    "samara": "Samara Martins",
    "leonardo avalanche": "Leonardo Avalanche",
    "avalanche": "Leonardo Avalanche",
    "wilson grassi": "Wilson Grassi",
    "grassi": "Wilson Grassi",
    "clariana barao": "Clariana Barão",
    "clariana": "Clariana Barão",
    "hertz dias": "Hertz Dias",
    "dias": "Hertz Dias",
    "edmilson costa": "Edmilson Costa",
    "costa": "Edmilson Costa",
    "rui costa pimenta": "Rui Costa Pimenta",
    "pimenta": "Rui Costa Pimenta",
    "aecio neves": "Aécio Neves",
    "aecio": "Aécio Neves",
    "cabo daciolo": "Cabo Daciolo",
    "daciolo": "Cabo Daciolo",
    "joaquim barbosa": "Joaquim Barbosa",
    "barbosa": "Joaquim Barbosa",
    "hero bezerra": "Heró Bezerra",
    "jair bolsonaro": "Jair Bolsonaro",
    "jair messias bolsonaro": "Jair Bolsonaro",
    "j. bolsonaro": "Jair Bolsonaro",
    "michelle bolsonaro": "Michelle Bolsonaro",
    "m. bolsonaro": "Michelle Bolsonaro",
    "fernando haddad": "Fernando Haddad",
    "haddad": "Fernando Haddad",
    "tarcisio de freitas": "Tarcísio de Freitas",
    "tarcisio": "Tarcísio de Freitas",
    "freitas": "Tarcísio de Freitas",
    "ratinho junior": "Ratinho Júnior",
    "ratinho": "Ratinho Júnior",
    "ciro gomes": "Ciro Gomes",
    "gomes": "Ciro Gomes",
    "simone tebet": "Simone Tebet",
    "tebet": "Simone Tebet",
    "geraldo alckmin": "Geraldo Alckmin",
    "eduardo leite": "Eduardo Leite",
    "leite": "Eduardo Leite",
    "helder barbalho": "Helder Barbalho",
    "barbalho": "Helder Barbalho",
    "tereza cristina": "Tereza Cristina",
    "cristina": "Tereza Cristina",
    "sergio moro": "Sergio Moro",
    "moro": "Sergio Moro",
    "aldo rebelo": "Aldo Rebelo",
    "michel temer": "Michel Temer",
    "rogerio marinho": "Rogério Marinho",
    "damares alves": "Damares Alves",
    "marcos pontes": "Marcos Pontes",
    "atila maia": "Átila Maia",
}

_POLLSTER_ALIASES: dict[str, str] = {
    "datafolha": "Datafolha",
    "quaest": "Quaest",
    "atlasintel": "AtlasIntel",
    "atlas": "AtlasIntel",
    "nexus": "Nexus",
    "poderdata": "PoderData",
    "futura": "Futura",
    "futurainteligencia": "Futura",
    "mda": "MDA",
    "ideia": "Ideia",
    "ideiabigdata": "Ideia",
    "realtimebigdata": "Real Time Big Data",
    "realtime": "Real Time Big Data",
    "paranapesquisas": "Paraná Pesquisas",
    "ipec": "Ipec",
    "ipsosipec": "Ipsos-Ipec",
    "ipespe": "Ipespe",
    "ibope": "Ibope",
    "gerp": "Gerp",
    "verita": "Veritá",
    "palver": "Palver",
    "voxbrasil": "Vox Brasil",
    "voxpopuli": "Vox Populi",
    "indexa": "Indexa",
    "datatrends": "DataTrends",
    "alfa": "Alfa Inteligência",
    "alfainteligencia": "Alfa Inteligência",
    "americananalytics": "American Analytics",
}

_PARTICLES = {"da", "de", "do", "das", "dos", "e", "di", "du"}


def _strip_party(s: str) -> str:
    return re.sub(r"\s*\([^()]*\)\s*$", "", s.strip()).strip()


def canonical_candidate(raw: str) -> str:
    """Canonical short name for a candidate given a link title, header text or footnote name."""
    name = _strip_party(raw)
    f = _fold(name)
    if f in _CANDIDATE_ALIASES:
        return _CANDIDATE_ALIASES[f]
    toks = f.split(" ")
    if toks and toks[0] in _CANDIDATE_ALIASES and len(toks) == 2 and len(toks[1]) <= 12 and toks[1].isupper():
        return _CANDIDATE_ALIASES[toks[0]]
    return name


def _person_name(text: str) -> str | None:
    """Canonical name if `text` is just a person's name (optionally with '(PARTY)'), else None."""
    name = _strip_party(text).rstrip(".")
    if not name or len(name) > 45 or re.search(r"[\d,;:%\"“”«»]", name):
        return None
    toks = name.split()
    if not 1 <= len(toks) <= 5:
        return None
    for t in toks:
        if t.lower() in _PARTICLES:
            continue
        if not t[0].isupper():
            return None
    return canonical_candidate(name)


def _pollster_alias(part: str) -> tuple[str | None, bool]:
    """(canonical pollster, was_fuzzy) for one name part; fuzzy only for near-identical long names."""
    k = _key(part)
    if k in _POLLSTER_ALIASES:
        return _POLLSTER_ALIASES[k], False
    if len(k) >= 6:
        best = max(_POLLSTER_ALIASES, key=lambda a: SequenceMatcher(None, k, a).ratio())
        if len(best) >= 6 and SequenceMatcher(None, k, best).ratio() >= 0.9:
            return _POLLSTER_ALIASES[best], True
    return None, False


def canonical_pollster(raw: str, contractor_first: bool = False) -> tuple[str, str, str]:
    """(pollster, contractor, note) from a cell like 'CNT/MDA', 'Nexus/BTG Pactual', 'AtlasIntel'."""
    text = re.sub(r"\s+", " ", raw).strip()
    parts = [p.strip() for p in re.split(r"\s*/\s*", text) if p.strip()]
    for fuzzy_ok in (False, True):
        for i, p in enumerate(parts):
            name, fuzzy = _pollster_alias(p)
            if name and (fuzzy_ok or not fuzzy):
                rest = [q for j, q in enumerate(parts) if j != i]
                return name, "/".join(rest), (f"pollster_name_normalised_from:{p}" if fuzzy else "")
    if len(parts) > 1 and contractor_first:
        return parts[-1], "/".join(parts[:-1]), ""
    return text, "", ""


# --------------------------------------------------------------------------------------------------
# value parsers

_MONTHS = {
    "jan": 1, "fev": 2, "feb": 2, "mar": 3, "abr": 4, "apr": 4, "mai": 5, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "aug": 8, "set": 9, "sep": 9, "out": 10, "oct": 10, "nov": 11, "dez": 12, "dec": 12,
}  # fmt: skip

_ABSENT = {"", "-", "\u2013", "\u2014", "\u2012", "n/a", "na", "n/d", "nd", "x", "\u00d7", "\u2014n/a"}


def parse_share(text: str) -> tuple[str, float | None]:
    """('value', v) | ('below', bound) | ('absent', None) | ('bad', None)."""
    t = _fold(text).replace("\u2212", "-").replace(" ", "")
    if t in _ABSENT:
        return "absent", None
    m = re.fullmatch(r"(<|≤|<=|menosde)(\d+(?:[.,]\d+)?)%*", t)
    if m:
        return "below", float(m.group(2).replace(",", "."))
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)%*", t)
    if m:
        v = float(m.group(1).replace(",", "."))
        return ("value", v) if 0 <= v <= 100 else ("bad", None)
    return "bad", None


def parse_sample(text: str) -> int | None:
    m = re.search(r"\d[\d\s.,\u00a0\u202f\u2009]*", text or "")
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(0))
    if not digits:
        return None
    n = int(digits)
    return n if 100 <= n <= 200_000 else None


def _parse_date_part(s: str) -> tuple[int | None, int | None, int | None]:
    m = re.fullmatch(r"(\d{1,2})(?:º|°)?(?:\s*(?:de\s+)?([A-Za-zÀ-ÿ]{3,})\.?)?(?:\s*(?:de\s+)?(\d{4}))?", s.strip())
    if not m:
        return None, None, None
    day = int(m.group(1))
    mon = None
    if m.group(2):
        mon = _MONTHS.get(_fold(m.group(2))[:3])
        if mon is None:
            return None, None, None
    year = int(m.group(3)) if m.group(3) else None
    return day, mon, year


def parse_dates(text: str, context_year: int | None) -> tuple[date, date] | None:
    """'28 Set - 30 Set', '23 - 28 Jul', '30 Aug - 2 Sep', '3 Out', '28 Dez 2025 - 3 Jan 2026'."""
    t = re.sub(r"\s+", " ", (text or "").replace("\u2212", "-")).strip()
    if not t:
        return None
    parts = re.split(r"\s*(?:[-\u2013\u2014\u2012]|\ba\b|\bto\b|\bate\b|\baté\b)\s*", t)
    parts = [p for p in parts if p]
    if len(parts) == 1:
        parts = parts * 2
    if len(parts) != 2:
        return None
    d1, m1, y1 = _parse_date_part(parts[0])
    d2, m2, y2 = _parse_date_part(parts[1])
    if d1 is None or d2 is None or m2 is None:
        return None
    y2 = y2 or context_year
    if y2 is None:
        return None
    m1 = m1 or m2
    if y1 is None:
        y1 = y2 - 1 if (m1, d1) > (m2, d2) else y2
    try:
        start, end = date(y1, m1, d1), date(y2, m2, d2)
    except ValueError:
        return None
    return (start, end) if start <= end else None


# --------------------------------------------------------------------------------------------------
# table grid


def _span(el, attr: str) -> int:
    m = re.match(r"\s*(\d+)", el.get(attr) or "1")
    return max(1, min(int(m.group(1)), 500)) if m else 1


def _rows(table) -> list:
    return table.xpath("./tr|./thead/tr|./tbody/tr|./tfoot/tr")


def _take_carry(carry: dict[int, list], row: list, col: int) -> int:
    """Append cells carried down by rowspans starting at `col`; return the next free column."""
    while col in carry:
        cell, left = carry[col]
        row.append(cell)
        if left <= 1:
            del carry[col]
        else:
            carry[col][1] = left - 1
        col += 1
    return col


def table_grid(table) -> list[list]:
    """Expand row/col spans: grid[r][c] is the cell element covering (r, c) (or None)."""
    grid: list[list] = []
    carry: dict[int, list] = {}  # col -> [cell, rows_left]
    for tr in _rows(table):
        row: list = []
        col = 0
        for cell in tr.xpath("./th|./td"):
            col = _take_carry(carry, row, col)
            cs, rs = _span(cell, "colspan"), _span(cell, "rowspan")
            for _ in range(cs):
                row.append(cell)
                if rs > 1:
                    carry[col] = [cell, rs - 1]
                col += 1
        col = _take_carry(carry, row, col)
        while carry and max(carry) >= col:  # carried cells beyond this row's own cells
            if col in carry:
                col = _take_carry(carry, row, col)
            else:
                row.append(None)
                col += 1
        grid.append(row)
    width = max((len(r) for r in grid), default=0)
    for r in grid:
        r.extend([None] * (width - len(r)))
    return grid


_RE_BLANK = re.compile(r"\bbrancos?\b|\bnulos?\b|\bblank\b|\bnull\b|\babsent|\babsten|\bnenhum|\bnone\b|\bninguem\b")
_RE_UNDEC = re.compile(r"\bindecis|\bundec|\bnao sab|\bns\b|\bnr\b|\bdon.?t know|\bnao resp|\bunsure\b")


def _classify_header(texts: list[str]) -> str | None:
    t = _fold(" ".join(x for x in texts if x))
    if not t:
        return None
    if re.search(r"^data\b|\bdata\(s\)|\bdatas?\b|period|\bdates?\b|fieldwork|realizac", t):
        return "dates"
    if re.search(r"contratante|pollster|polling firm|\binstituto\b", t):
        return "pollster"
    if re.search(r"amostra|sample|\bentrevist", t):
        return "sample"
    if re.search(r"margem|margin|\berro\b|\berror\b", t):
        return "margin"
    if re.search(r"vantagem|\blead\b|diferenca", t):
        return "lead"
    if re.search(r"^links?$|^ref|^fonte|^source", t):
        return "link"
    if re.search(r"\bmodo\b|\bmode\b|metodolog|method", t):
        return "methodology"
    if re.search(r"\bpublica|divulga|published|release", t):  # \b: party label "Republicanos" is not a date
        return "publication"
    if re.search(r"registro|registration|\btse\b", t):
        return "tse"
    if re.search(r"^outros?\b|^others?\b|^demais\b", t):
        return "others"
    blank, undec = bool(_RE_BLANK.search(t)), bool(_RE_UNDEC.search(t))
    if blank and undec:
        return "bn_und"
    if blank:
        return "blank_null"
    if undec:
        return "undecided"
    if re.search(r"empresa|pesquisa|\bfirm\b|polling", t):
        return "pollster"
    return None


# --------------------------------------------------------------------------------------------------
# parsing


@dataclass
class _Scenario:
    cands: dict[str, float] = field(default_factory=dict)  # canonical -> share (may include OTHERS)
    present: set[str] = field(default_factory=set)  # named candidates offered (numeric or "<x%")
    order: list[str] = field(default_factory=list)
    blank_null: float = np.nan
    undecided: float = np.nan
    notes: list[str] = field(default_factory=list)


@dataclass
class _Poll:
    pollster: str
    contractor: str
    field_start: date
    field_end: date
    sample_size: int
    methodology: str = ""
    publication_date: str = ""
    tse_br_id: str = ""
    basis: str = "total"
    scenarios: list[_Scenario] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


_OTHERS_RE = re.compile(
    r"^(outros?( candidatos?)?( nao especificados?)?|outro candidato.*|others?( not specified| unspecified)?"
    r"|unspecified( others)?|demais( candidatos)?|nao especificados?)$"
)
_NOT_RELEASED_RE = re.compile(r"nao divulgad|a divulgar|not (yet )?(been )?released|to be released|nao publicad")
_EXCLUDE_CONTEXT_RE = re.compile(
    r"espontane|spontaneous|rejei|rejection|segundo turno|second round|runoff|\bestad|\bstate\b|regional"
    r"|governador|governor|senado|senate"
)
# runoff section: the same exclusions except the runoff terms themselves
_EXCLUDE_CONTEXT_R2_RE = re.compile(
    r"espontane|spontaneous|rejei|rejection|\bestad|\bstate\b|regional|governador|governor|senado|senate"
)
_SECTION_RE = {
    1: re.compile(r"primeiro turno|first round|1o turno|1st round"),
    2: re.compile(r"segundo turno|second round|2o turno|2nd round|runoff"),
}
_VALID_CONTEXT_RE = re.compile(r"votos validos|valid votes")


def _clean_note(s: str, limit: int = 90) -> str:
    s = re.sub(r"\s+", " ", s.replace(";", ",")).strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


class _Doc:
    def __init__(self, html: str):
        self.doc = lxml_html.fromstring(html)
        self.refs: dict[str, object] = {}
        for el in self.doc.xpath('//*[starts-with(@id, "cite_note")]'):
            self.refs[el.get("id")] = el

    def ref_text(self, rid: str) -> str:
        el = self.refs.get(rid)
        if el is None:
            return ""
        rt = el.xpath('.//*[contains(concat(" ", normalize-space(@class), " "), " reference-text ")]')
        target = rt[0] if rt else el
        return re.sub(r"\s+", " ", target.text_content()).strip()


def _resolve_others(kind: str, value: float | None, notes: list[str]) -> tuple[list[tuple[str, float]], set, list]:
    """Attribute an Outros/Others cell: ([(name, share)], {present-but-below names}, notes)."""
    for ft in notes:
        f = _fold(ft).rstrip(".")
        if _OTHERS_RE.match(f):
            break  # explicitly unspecified others
        parts = [p.strip() for p in ft.split(";") if p.strip()]
        if kind == "value" and any(":" in p for p in parts):
            parsed: list[tuple[str, float]] = []
            for p in parts:
                m = re.fullmatch(r"(?P<name>[^:]+?)\s*:\s*(?P<val>\d+(?:[.,]\d+)?)\s*%?\.?", p) or re.fullmatch(
                    r"(?P<val>\d+(?:[.,]\d+)?)\s*%?\s*:\s*(?P<name>[^:]+?)\.?", p
                )
                if not m:
                    parsed = []
                    break
                nm = _strip_party(m.group("name"))
                cname = OTHERS if _OTHERS_RE.match(_fold(nm)) else _person_name(nm)
                if cname is None:
                    parsed = []
                    break
                parsed.append((cname, float(m.group("val").replace(",", "."))))
            names = [n for n, _ in parsed]
            if parsed and len(set(names)) == len(names) and abs(sum(v for _, v in parsed) - value) <= 0.15:
                return parsed, set(), ["others_split_from_footnote"]
        if len(parts) == 1:
            nm = _person_name(parts[0])
            if nm:
                if kind == "below":
                    return [], {nm}, [f"below_threshold:{nm}<{value:g}"]
                return [(nm, value)], set(), ["others_column_named_candidate"]
    if kind == "below":
        return [], set(), [f"below_threshold:{OTHERS}<{value:g}"]
    out_notes = [f"others_footnote:{_clean_note(n)}" for n in notes if not _OTHERS_RE.match(_fold(n).rstrip("."))]
    return [(OTHERS, value)], set(), out_notes


def _iter_section_tables(d: _Doc, round_: int):
    """Yield (table, context dict) for top-level wikitables in the h2 section of `round_`, in order."""
    section_re = _SECTION_RE[round_]
    in_first = False
    year: int | None = None
    h3 = h4 = ""
    for el in d.doc.iter():
        if not isinstance(el.tag, str):
            continue
        tag = el.tag
        if tag == "h2":
            t = _fold(_text(el))
            in_first = bool(section_re.search(t))
            year, h3, h4 = None, "", ""
        elif tag == "h3" or (tag == "div" and "hidden-title" in (el.get("class") or "")):
            t = _text(el)
            m = re.search(r"\b(20\d{2})\b", t)
            year = int(m.group(1)) if m else None
            h3, h4 = t, ""
        elif tag in ("h4", "h5"):
            t = _text(el)
            h4 = t
            m = re.search(r"\b(20\d{2})\b", t)
            if m:
                year = int(m.group(1))
        elif tag == "table" and in_first and "wikitable" in (el.get("class") or ""):
            if any(a.tag == "table" for a in el.iterancestors()):
                continue
            cap = el.find("caption")
            yield el, {"year": year, "h3": h3, "h4": h4, "caption": _text(cap) if cap is not None else ""}


def _header_info(grid: list[list]) -> tuple[int, dict[int, str], dict[int, str], bool]:
    """(n_header_rows, col -> semantic, col -> candidate name, contractor_first)."""
    n_head = 0
    for row in grid:
        tds = [c for c in dict.fromkeys(row) if c is not None and c.tag == "td"]
        if any(_text(c) for c in tds):
            break
        n_head += 1
    sem: dict[int, str] = {}
    cand: dict[int, str] = {}
    contractor_first = False
    width = len(grid[0]) if grid else 0
    for j in range(width):
        cells = list(dict.fromkeys(grid[i][j] for i in range(n_head) if grid[i][j] is not None))
        texts = [_text(c) for c in cells]
        s = _classify_header(texts)
        if s:
            sem[j] = s
            if s == "pollster" and "contratante" in _fold(" ".join(texts)).split("/")[0]:
                contractor_first = True
            continue
        name = None
        for c in reversed(cells):  # most specific (lowest) header cell first
            if c.get("colspan") and _span(c, "colspan") > 1:
                continue
            link = _first_person_link(c)
            if link:
                name = canonical_candidate(link)
                break
        if name is None:
            for c, t in zip(reversed(cells), reversed(texts), strict=True):
                if t and _span(c, "colspan") == 1:
                    name = canonical_candidate(t)
                    break
        if name:
            sem[j] = "candidate"
            cand[j] = name
    return n_head, sem, cand, contractor_first


def parse(
    html: str, rec: SourceRecord, lang: str, *, min_field_end: str | None = MIN_FIELD_END, round_: int = ROUND
) -> pd.DataFrame:
    """Parse one pinned revision into canonical long rows (national, stimulated, 2026).

    ``round_=1``: first-round tables. ``round_=2``: runoff head-to-head tables; each scenario is one
    pairing with exactly two named candidates, labelled "<A> vs <B>" (canonical names, ``sorted`` order).
    Parse diagnostics are attached as ``df.attrs["parse_stats"]`` and ``df.attrs["skipped"]``.
    """
    if round_ not in _SECTION_RE:
        raise ValueError(f"round_ must be 1 or 2, got {round_!r}")
    exclude_re = _EXCLUDE_CONTEXT_RE if round_ == 1 else _EXCLUDE_CONTEXT_R2_RE
    d = _Doc(html)
    retrieved = pd.Timestamp(rec.retrieval_timestamp).date() if rec.retrieval_timestamp else None
    min_end = date.fromisoformat(min_field_end) if min_field_end else None
    polls: dict[str, _Poll] = {}
    skipped: list[dict] = []
    stats: Counter = Counter()

    for t_idx, (table, ctx) in enumerate(_iter_section_tables(d, round_)):
        context_text = _fold(" ".join([ctx["h3"], ctx["h4"], ctx["caption"]]))
        if exclude_re.search(context_text):
            stats["tables_excluded_context"] += 1
            continue
        grid = table_grid(table)
        if not grid:
            continue
        n_head, sem, cand_cols, contractor_first = _header_info(grid)
        cols = {s: [j for j, v in sem.items() if v == s] for s in set(sem.values())}
        if not ({"pollster", "dates", "sample"} <= cols.keys()) or not cand_cols:
            stats["tables_not_poll_tables"] += 1
            continue
        header_text = _fold(" ".join(_text(c) for r in grid[:n_head] for c in dict.fromkeys(r) if c is not None))
        basis = "valid" if _VALID_CONTEXT_RE.search(context_text + " " + header_text) else "total"
        stats["tables_parsed"] += 1
        pcol, dcol, ncol = cols["pollster"][0], cols["dates"][0], cols["sample"][0]
        cand_order = sorted(cand_cols)

        groups: list[tuple[object, list[int]]] = []  # (pollster cell, data-row indices)
        for r in range(n_head, len(grid)):
            row = grid[r]
            pc = row[pcol]
            if pc is None:
                continue
            if groups and groups[-1][0] is pc:
                groups[-1][1].append(r)
            else:
                groups.append((pc, [r]))

        for pc, rows in groups:
            where = {"lang": lang, "table": t_idx, "heading": f"{ctx['h3']} / {ctx['h4']}", "row": rows[0]}
            ptext = _text(pc)
            if _span(pc, "colspan") > 1 or not ptext:
                stats["event_rows"] += len(rows)
                continue
            first = grid[rows[0]]
            dates = parse_dates(_text(first[dcol]) if first[dcol] is not None else "", ctx["year"])
            n = parse_sample(_text(first[ncol])) if first[ncol] is not None else None
            snippet = " | ".join(_text(c) for c in dict.fromkeys(first) if c is not None)[:120]
            # results area covered by one wide cell -> not released / no results
            cand_cells = [first[j] for j in cand_order]
            wide_min = 3 if round_ == 1 else min(3, max(2, len(cand_order)))  # PT runoff tables: 2 columns
            wide = [c for c in dict.fromkeys(cand_cells) if c is not None and cand_cells.count(c) >= wide_min]
            if wide:
                wt = _fold(_text(wide[0]))
                reason = "not_released" if _NOT_RELEASED_RE.search(wt) else "no_results_row"
                stats[reason] += len(rows)
                skipped.append({**where, "reason": reason, "text": snippet})
                continue
            if dates is None or n is None:
                reason = "bad_dates" if dates is None else "bad_sample_size"
                stats["skipped_unparseable"] += len(rows)
                skipped.append({**where, "reason": reason, "text": snippet})
                continue
            fs, fe = dates
            if retrieved is not None and fe > retrieved + timedelta(days=1):
                stats["skipped_unparseable"] += len(rows)
                skipped.append({**where, "reason": "future_field_end", "text": snippet})
                continue
            if min_end is not None and fe < min_end:
                stats["rows_before_min_field_end"] += len(rows)
                continue
            pname = re.sub(r"\s*\(?\s*BR-?\s?\d{5}/\d{4}\s*\)?\s*", " ", ptext).strip()
            pollster, contractor, pnote = canonical_pollster(pname, contractor_first=contractor_first)
            meta_notes: list[str] = [pnote] if pnote else []
            methodology = _text(first[cols["methodology"][0]]) if "methodology" in cols else ""
            publication = ""
            if "publication" in cols:
                pd_ = parse_dates(_text(first[cols["publication"][0]]), ctx["year"])
                publication = pd_[1].isoformat() if pd_ else ""
            # TSE registration: explicit column, or in the text of the references this poll cites
            tse = ""
            hay = [ptext] + ([_text(first[cols["tse"][0]])] if "tse" in cols else [])
            for r in rows:
                for c in dict.fromkeys(grid[r]):
                    if c is not None:
                        hay.extend(d.ref_text(rid) for _, rid in _footnote_refs(c))
            ids = list(dict.fromkeys(m.group(0) for h in hay for m in re.finditer(r"BR-?\s?\d{5}/2026", h)))
            if ids:
                tse = re.sub(r"BR-?\s?", "BR-", ids[0])
                if len(ids) > 1:
                    meta_notes.append("multiple_tse_ids_in_refs:" + ",".join(ids))

            scen_list: list[_Scenario] = []
            for r in rows:
                row = grid[r]
                sc = _Scenario()
                bad_cells = 0
                for j in cand_order:
                    c = row[j]
                    if c is None:
                        continue
                    kind, v = parse_share(_text(c))
                    name = cand_cols[j]
                    if kind == "value":
                        if name in sc.cands:
                            sc.notes.append(f"duplicate_column:{name}")
                            continue
                        sc.cands[name] = v
                        sc.present.add(name)
                        sc.order.append(name)
                    elif kind == "below":
                        sc.present.add(name)
                        sc.notes.append(f"below_threshold:{name}<{v:g}")
                    elif kind == "bad":
                        bad_cells += 1
                        sc.notes.append(f"unparsed_cell:{name}={_clean_note(_text(c), 20)}")
                for j in cols.get("others", []):
                    c = row[j]
                    if c is None:
                        continue
                    kind, v = parse_share(_text(c))
                    if kind not in ("value", "below"):
                        continue
                    fnotes = [d.ref_text(rid) for lab, rid in _footnote_refs(c) if _is_note_label(lab)]
                    entries, below_names, notes = _resolve_others(kind, v, [x for x in fnotes if x])
                    if any(nm in sc.cands for nm, _ in entries if nm != OTHERS) or OTHERS in sc.cands:
                        entries, below_names = [(OTHERS, v)], set()
                        notes = ["others_footnote_collision"]
                        if kind == "below":
                            entries, notes = [], [f"below_threshold:{OTHERS}<{v:g}"]
                    for nm, val in entries:
                        sc.cands[nm] = val
                        sc.order.append(nm)
                        if nm != OTHERS:
                            sc.present.add(nm)
                    sc.present |= below_names
                    sc.notes.extend(notes)
                for s_name in ("bn_und", "blank_null", "undecided"):
                    for j in cols.get(s_name, []):
                        c = row[j]
                        if c is None:
                            continue
                        kind, v = parse_share(_text(c))
                        if kind != "value":
                            if kind == "below":
                                sc.notes.append(f"below_threshold:{s_name}<{v:g}")
                            continue
                        if s_name == "undecided":
                            sc.undecided = v if np.isnan(sc.undecided) else sc.undecided + v
                        else:
                            sc.blank_null = v if np.isnan(sc.blank_null) else sc.blank_null + v
                            if s_name == "bn_und":
                                sc.notes.append("blank_null_includes_undecided")
                named_numeric = [k for k in sc.cands if k != OTHERS]
                if not named_numeric:
                    stats["skipped_unparseable"] += 1
                    skipped.append({**where, "row": r, "reason": "no_numeric_candidate_values", "text": snippet})
                    continue
                if round_ == 2 and (len(named_numeric) != 2 or OTHERS in sc.cands or sc.present != set(named_numeric)):
                    stats["skipped_not_two_candidates"] += 1
                    skipped.append({**where, "row": r, "reason": "runoff_row_not_two_candidates", "text": snippet})
                    continue
                if bad_cells:
                    stats["cells_unparsed"] += bad_cells
                scen_list.append(sc)
            if not scen_list:
                continue
            pid = make_poll_id(ELECTION, round_, pollster, fs.isoformat(), fe.isoformat(), n)
            if pid in polls:  # same poll listed in separate rows -> further scenarios
                polls[pid].scenarios.extend(scen_list)
                stats["polls_merged_from_separate_rows"] += 1
            else:
                polls[pid] = _Poll(pollster, contractor, fs, fe, n, methodology, publication, tse, basis,
                                   scen_list, meta_notes)  # fmt: skip
            stats["scenario_rows_parsed"] += len(scen_list)

    rows_out: list[dict] = []
    for pid, p in polls.items():
        if round_ == 2:
            p.scenarios, n_dup = _dedupe_scenarios(p.scenarios)
            stats["duplicate_scenarios_dropped"] += n_dup
            labels = _pair_labels(p.scenarios)
        else:
            labels = _scenario_labels(p.scenarios)
        for lab, sc in zip(labels, p.scenarios, strict=True):
            notes = _compact_notes(p.notes + sc.notes)
            for nm in sc.order:
                rows_out.append(
                    {
                        "poll_id": pid,
                        "election": ELECTION,
                        "round": round_,
                        "tse_br_id": p.tse_br_id,
                        "pollster": p.pollster,
                        "contractor": p.contractor,
                        "field_start": p.field_start.isoformat(),
                        "field_end": p.field_end.isoformat(),
                        "publication_date": p.publication_date,
                        "sample_size": int(p.sample_size),
                        "methodology": p.methodology,
                        "scenario": lab,
                        "candidate": nm,
                        "share_reported": float(sc.cands[nm]),
                        "share_basis": p.basis,
                        "blank_null": sc.blank_null,
                        "undecided": sc.undecided,
                        "valid_vote_share": np.nan,
                        "source_url": rec.url,
                        "source_type": "wikipedia_revision",
                        "source_revision": str(rec.revision),
                        "retrieval_timestamp": rec.retrieval_timestamp,
                        "verification_status": "unverified",
                        "verification_source": "",
                        "source_hash": rec.sha256,
                        "notes": notes,
                    }
                )
    df = pd.DataFrame(rows_out, columns=CANONICAL_COLUMNS)
    df["round"] = df["round"].astype(int)
    df["sample_size"] = df["sample_size"].astype(int)
    for c in ("share_reported", "blank_null", "undecided", "valid_vote_share"):
        df[c] = df[c].astype(float)
    stats["polls"] = len(polls)
    df.attrs["parse_stats"] = dict(stats)
    df.attrs["skipped"] = skipped
    return df


def _compact_notes(notes: list[str]) -> str:
    """Join notes with ';' (deduplicated), merging all below-threshold items into one note."""
    below = [n.split(":", 1)[1] for n in notes if n.startswith("below_threshold:")]
    out = list(dict.fromkeys(n for n in notes if n and not n.startswith("below_threshold:")))
    if below:
        out.append("below_threshold:" + ",".join(dict.fromkeys(below)))
    return ";".join(out)


def _scenario_labels(scens: list[_Scenario]) -> list[str]:
    """Scenario labels naming the distinguishing candidates.

    'main' if the poll has one scenario. Otherwise, relative to the union U and the intersection C of
    the candidates offered across the poll's scenarios: 'full' if the scenario offers all of U, else the
    shorter of 'without <U - S>' and 'with <S - C>' (ties -> 'without'); duplicates get ' [k]'.
    """
    if len(scens) == 1:
        return ["main"]
    union = set().union(*(s.present for s in scens))
    core = set.intersection(*(set(s.present) for s in scens))
    labels = []
    for s in scens:
        missing = sorted(union - s.present, key=_fold)
        extra = sorted(s.present - core, key=_fold)
        if not missing:
            labels.append("full")
        elif extra and len(extra) < len(missing):
            labels.append("with " + ", ".join(extra))
        else:
            labels.append("without " + ", ".join(missing))
    seen: Counter = Counter()
    out = []
    for lab in labels:
        seen[lab] += 1
        out.append(lab if seen[lab] == 1 else f"{lab} [{seen[lab]}]")
    return out


def pair_label(a: str, b: str) -> str:
    """Runoff scenario label: the two canonical names in ``sorted`` order, e.g. 'Flávio Bolsonaro vs Lula'."""
    x, y = sorted((a, b))
    return f"{x} vs {y}"


def _pair_labels(scens: list[_Scenario]) -> list[str]:
    """Round-2 labels '<A> vs <B>'; a pairing listed twice with different values gets ' [k]'."""
    seen: Counter = Counter()
    out = []
    for s in scens:
        a, b = (k for k in s.cands if k != OTHERS)
        lab = pair_label(a, b)
        seen[lab] += 1
        if seen[lab] > 1:
            s.notes.append("pairing_listed_twice_with_different_values")
        out.append(lab if seen[lab] == 1 else f"{lab} [{seen[lab]}]")
    return out


def _same_values(a: float, b: float) -> bool:
    return (np.isnan(a) and np.isnan(b)) or a == b


def _dedupe_scenarios(scens: list[_Scenario]) -> tuple[list[_Scenario], int]:
    """Drop exact repeats of a scenario (same shares, blank/null and undecided), e.g. a poll listed in two
    tables of the same pairing. Returns (kept scenarios, number dropped)."""
    kept: list[_Scenario] = []
    for s in scens:
        if any(
            k.cands == s.cands and _same_values(k.blank_null, s.blank_null) and _same_values(k.undecided, s.undecided)
            for k in kept
        ):
            continue
        kept.append(s)
    return kept, len(scens) - len(kept)


# --------------------------------------------------------------------------------------------------
# reconciliation


def _poll_summaries(df: pd.DataFrame) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for pid, g in df.groupby("poll_id", sort=False):
        first = g.iloc[0]
        scen = {}
        for lab, gs in g.groupby("scenario", sort=False):
            named = gs[gs["candidate"] != OTHERS]
            oth = gs.loc[gs["candidate"] == OTHERS, "share_reported"]
            scen[lab] = {
                "cands": dict(zip(named["candidate"], named["share_reported"], strict=True)),
                "oth": float(oth.iloc[0]) if len(oth) else np.nan,
                "has_below": "below_threshold" in str(gs["notes"].iloc[0]),
                "bn": gs["blank_null"].iloc[0],
                "und": gs["undecided"].iloc[0],
            }
        out[pid] = {
            "poll_id": pid,
            "pollster": first["pollster"],
            "pkey": _key(first["pollster"]),
            "fs": date.fromisoformat(first["field_start"]),
            "fe": date.fromisoformat(first["field_end"]),
            "n": int(first["sample_size"]),
            "rev": str(first["source_revision"]),
            "scen": scen,
        }
    return out


def _overlap(a: dict, b: dict) -> bool:
    return a["fs"] <= b["fe"] and b["fs"] <= a["fe"]


def _match_polls(pt: dict[str, dict], en: dict[str, dict]) -> dict[str, tuple[str, str]]:
    """en poll_id -> (pt poll_id, tier). Tiers are tried in order; each needs a unique mutual candidate."""

    tiers = [
        ("exact", lambda e, p: e["fe"] == p["fe"] and e["n"] == p["n"]),
        ("field_end_pm1", lambda e, p: abs((e["fe"] - p["fe"]).days) <= 1 and e["n"] == p["n"]),
        ("same_dates_n_differs", lambda e, p: e["fs"] == p["fs"] and e["fe"] == p["fe"]),
        (
            "overlap_same_n",
            lambda e, p: (
                _overlap(e, p) and e["n"] == p["n"] and (e["fs"] == p["fs"] or abs((e["fe"] - p["fe"]).days) <= 3)
            ),
        ),
        (
            "overlap_n_within_1pct",
            lambda e, p: (
                _overlap(e, p)
                and abs((e["fe"] - p["fe"]).days) <= 2
                and abs(e["n"] - p["n"]) <= 0.01 * max(e["n"], p["n"])
            ),
        ),
    ]
    matched: dict[str, tuple[str, str]] = {}
    used_pt: set[str] = set()
    for tier, ok in tiers:
        cand_e: dict[str, list[str]] = {}
        for eid, e in en.items():
            if eid in matched:
                continue
            cand_e[eid] = [pid for pid, p in pt.items() if pid not in used_pt and p["pkey"] == e["pkey"] and ok(e, p)]
        cand_p: Counter = Counter(pid for lst in cand_e.values() for pid in lst)
        for eid, lst in cand_e.items():
            if len(lst) == 1 and cand_p[lst[0]] == 1:
                matched[eid] = (lst[0], tier)
                used_pt.add(lst[0])
    return matched


def _pair_scenarios(ps: dict[str, dict], es: dict[str, dict]) -> tuple[list[tuple[str, str]], int]:
    """Pair PT and EN scenarios of one poll; returns (pairs, number of unpaired EN scenarios)."""
    if len(ps) == 1 and len(es) == 1:
        return [(next(iter(ps)), next(iter(es)))], 0
    en_names = set().union(*(set(v["cands"]) for v in es.values()))
    free = dict(ps)
    pairs, unpaired = [], 0
    for elab, ev in es.items():
        key = set(ev["cands"])
        cands = [plab for plab, pv in free.items() if set(pv["cands"]) & en_names == key]
        if not cands:
            unpaired += 1
            continue

        def dist(plab: str, ev: dict = ev) -> float:
            pv = free[plab]["cands"]
            return sum(abs(pv[c] - ev["cands"][c]) for c in set(pv) & set(ev["cands"]))

        best = min(cands, key=dist)
        pairs.append((best, elab))
        del free[best]
    return pairs, unpaired


_POLL_META_COLUMNS = [
    "poll_id",
    "election",
    "round",
    "tse_br_id",
    "pollster",
    "contractor",
    "field_start",
    "field_end",
    "publication_date",
    "sample_size",
    "methodology",
]


def reconcile(pt_df: pd.DataFrame, en_df: pd.DataFrame, *, round_: int = ROUND) -> tuple[pd.DataFrame, pd.DataFrame]:
    """PT is primary. EN-only polls are appended (notes 'gap_fill_en'); PT/EN differences -> conflicts.

    Round 2 only: scenarios of a matched poll are paired by their pairing label, and an EN pairing that PT
    does not list for that poll is appended under the PT poll (PT poll metadata, EN values and provenance,
    note 'gap_fill_en_scenario')."""
    pt, en = _poll_summaries(pt_df), _poll_summaries(en_df)
    matched = _match_polls(pt, en)
    conflicts: list[dict] = []
    unpaired_en_scen = 0
    gap_scen: list[tuple[str, str, list[str]]] = []  # (pt poll_id, en poll_id, EN-only pairing labels)
    tier_counts: Counter = Counter(t for _, t in matched.values())
    for eid, (pid, tier) in matched.items():
        p, e = pt[pid], en[eid]
        base = {
            "poll_id": pid,
            "pollster": p["pollster"],
            "field_end": p["fe"].isoformat(),
            "pt_revision": p["rev"],
            "en_revision": e["rev"],
            "en_poll_id": eid,
            "match_tier": tier,
        }
        for kind, pv, ev in (
            ("field_start", p["fs"].isoformat(), e["fs"].isoformat()),
            ("field_end", p["fe"].isoformat(), e["fe"].isoformat()),
            ("sample_size", p["n"], e["n"]),
        ):
            if pv != ev:
                conflicts.append({**base, "candidate": f"__{kind}__", "pt_value": pv, "en_value": ev, "kind": kind,
                                  "pt_scenario": "", "en_scenario": ""})  # fmt: skip
        if round_ == 2:
            pairs = [(lab, lab) for lab in e["scen"] if lab in p["scen"]]
            en_only = [lab for lab in e["scen"] if lab not in p["scen"]]
            if en_only:
                gap_scen.append((pid, eid, en_only))
            unp = len(en_only)
        else:
            pairs, unp = _pair_scenarios(p["scen"], e["scen"])
        unpaired_en_scen += unp
        for plab, elab in pairs:
            ps, es = p["scen"][plab], e["scen"][elab]
            diffs = [("share", c, ps["cands"][c], es["cands"][c]) for c in ps["cands"] if c in es["cands"]]
            diffs += [
                ("blank_null", "__blank_null__", ps["bn"], es["bn"]),
                ("undecided", "__undecided__", ps["und"], es["und"]),
            ]
            # EN folds minor candidates into "Others": compare with PT's sum over the same candidates
            if not pd.isna(es["oth"]) and not ps["has_below"] and set(es["cands"]) <= set(ps["cands"]):
                agg = sum(v for c, v in ps["cands"].items() if c not in es["cands"])
                agg += 0.0 if pd.isna(ps["oth"]) else ps["oth"]
                diffs.append(("others_aggregate", "__others_aggregate__", round(agg, 6), es["oth"]))
            for kind, cname, pv, ev in diffs:
                if pd.isna(pv) or pd.isna(ev):
                    continue
                if round(abs(float(pv) - float(ev)), 6) > SHARE_TOL:
                    conflicts.append({**base, "candidate": cname, "pt_value": float(pv), "en_value": float(ev),
                                      "kind": kind, "pt_scenario": plab, "en_scenario": elab})  # fmt: skip
    gap_ids = [eid for eid in en if eid not in matched]
    dup_of: dict[str, list[str]] = {}
    for eid in gap_ids:
        e = en[eid]
        near = [pid for pid, p in pt.items() if p["pkey"] == e["pkey"] and _overlap(e, p)]
        if near:
            dup_of[eid] = near
            for pid in near:
                q = pt[pid]
                conflicts.append(
                    {
                        "poll_id": pid,
                        "pollster": q["pollster"],
                        "field_end": q["fe"].isoformat(),
                        "candidate": "__poll__",
                        "pt_value": f"{q['fs']}..{q['fe']} n={q['n']}",
                        "en_value": f"{e['fs']}..{e['fe']} n={e['n']}",
                        "pt_revision": q["rev"],
                        "en_revision": e["rev"],
                        "kind": "gap_fill_overlaps_pt_poll",
                        "pt_scenario": "",
                        "en_scenario": "",
                        "en_poll_id": eid,
                        "match_tier": "unmatched",
                    }
                )
    gap = en_df[en_df["poll_id"].isin(gap_ids)].copy()

    def _gap_note(row: pd.Series) -> str:
        extra = ["gap_fill_en"] + [f"possible_duplicate_of:{x}" for x in dup_of.get(row["poll_id"], [])]
        return ";".join([x for x in str(row["notes"] or "").split(";") if x and x != "nan"] + extra)

    if len(gap):
        gap["notes"] = gap.apply(_gap_note, axis=1)
    parts = [pt_df, gap]
    n_gap_scen = 0
    for pid, eid, labs in gap_scen:
        meta = pt_df.loc[pt_df["poll_id"] == pid, _POLL_META_COLUMNS].iloc[0]
        rows = en_df[(en_df["poll_id"] == eid) & en_df["scenario"].isin(labs)].copy()
        for c in _POLL_META_COLUMNS:
            rows[c] = meta[c]
        extra = "gap_fill_en_scenario" + ("" if eid == pid else f";en_poll_id:{eid}")
        rows["notes"] = [
            ";".join([x for x in str(n or "").split(";") if x and x != "nan"] + [extra]) for n in rows["notes"]
        ]
        parts.append(rows)
        n_gap_scen += len(labs)
    merged = pd.concat(parts, ignore_index=True)[CANONICAL_COLUMNS]
    merged = merged.sort_values(["field_end", "pollster", "poll_id"], ascending=[False, True, True], kind="stable")
    merged = merged.reset_index(drop=True)
    conf = pd.DataFrame(conflicts, columns=CONFLICT_COLUMNS)
    merged.attrs["reconcile_stats"] = {
        "pt_polls": len(pt),
        "en_polls": len(en),
        "matched": len(matched),
        "match_tiers": dict(tier_counts),
        "en_gap_fill_polls": len(gap_ids),
        "en_gap_fill_overlapping_a_pt_poll": len(dup_of),
        "pt_only_polls": len(set(pt) - {v[0] for v in matched.values()}),
        "unpaired_en_scenarios_in_matched_polls": unpaired_en_scen,
    }
    if round_ == 2:
        merged.attrs["reconcile_stats"]["en_gap_fill_scenarios_in_matched_polls"] = n_gap_scen
    return merged, conf


# --------------------------------------------------------------------------------------------------
# entry point


def _display_scenario(g: pd.DataFrame) -> pd.DataFrame:
    labs = list(dict.fromkeys(g["scenario"]))
    for pref in ("main", "full"):
        if pref in labs:
            return g[g["scenario"] == pref]
    return g[g["scenario"] == labs[0]]


def summarize(merged: pd.DataFrame, since: str = "2026-08-16", k: int = 15) -> None:
    polls = merged.drop_duplicates("poll_id")
    print(f"\n{k} most recent polls (display scenario = main/full/first):")
    for _, pr in polls.head(k).iterrows():
        g = _display_scenario(merged[merged["poll_id"] == pr["poll_id"]])
        named = g[g["candidate"] != OTHERS].sort_values("share_reported", ascending=False).head(4)
        top = ", ".join(f"{c} {s:g}" for c, s in zip(named["candidate"], named["share_reported"], strict=True))
        nsc = merged.loc[merged["poll_id"] == pr["poll_id"], "scenario"].nunique()
        src = "EN" if "gap_fill_en" in str(pr["notes"]) else "PT"
        print(
            f"  {pr['pollster']:<20} {pr['field_start']}..{pr['field_end']} n={pr['sample_size']:<6} "
            f"[{src}, {nsc} scen] {top} | bn={g['blank_null'].iloc[0]:g} und={g['undecided'].iloc[0]:g}"
        )
    recent = polls[polls["field_end"] >= since]
    print(f"\nPolls per pollster with field_end >= {since} (total {len(recent)}):")
    for name, cnt in recent["pollster"].value_counts().items():
        print(f"  {name:<22} {cnt}")
    rr = merged[merged["field_end"] >= since]
    cnt = rr[rr["candidate"] != OTHERS].groupby("candidate")["poll_id"].nunique().sort_values(ascending=False)
    print(f"\nCandidates in polls with field_end >= {since} (number of polls with a numeric share):")
    for name, c in cnt.items():
        print(f"  {name:<22} {c}")


def summarize_runoff(merged: pd.DataFrame, since: str = "2026-08-05") -> None:
    """Head-to-head polls per pairing (and per pollster) with field_end >= `since`."""
    rr = merged[merged["field_end"] >= since]
    by_pair = rr.groupby("scenario")["poll_id"].nunique().sort_values(ascending=False)
    print(f"\nRunoff head-to-head polls per pairing with field_end >= {since}:")
    for lab, c in by_pair.items():
        per = rr[rr["scenario"] == lab].drop_duplicates("poll_id")["pollster"].value_counts()
        print(f"  {lab:<34} {c:>3}  ({', '.join(f'{k} {v}' for k, v in per.items())})")


def build_tables(
    pt_html: str, pt_rec: SourceRecord, en_html: str, en_rec: SourceRecord, round_: int = ROUND
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Parse both revisions for one round, reconcile (PT primary) and keep field_end >= MIN_FIELD_END.

    Returns (polls, conflicts, diagnostics) without writing anything."""
    parse_min = (date.fromisoformat(MIN_FIELD_END) - timedelta(days=MATCH_SLACK_DAYS)).isoformat()
    pt_df = parse(pt_html, pt_rec, "pt", min_field_end=parse_min, round_=round_)
    en_df = parse(en_html, en_rec, "en", min_field_end=parse_min, round_=round_)
    merged, conflicts = reconcile(pt_df, en_df, round_=round_)
    rstats = merged.attrs["reconcile_stats"]
    merged = merged[merged["field_end"] >= MIN_FIELD_END].reset_index(drop=True)
    conflicts = conflicts[conflicts["poll_id"].isin(set(merged["poll_id"]))].reset_index(drop=True)
    diag = {
        "parse_stats": {"pt": pt_df.attrs["parse_stats"], "en": en_df.attrs["parse_stats"]},
        "skipped": {"pt": pt_df.attrs["skipped"], "en": en_df.attrs["skipped"]},
        "reconcile_stats": rstats,
    }
    return merged, conflicts, diag


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def _report(merged: pd.DataFrame, conflicts: pd.DataFrame, diag: dict, label: str) -> None:
    print(f"\n===== {label}")
    for lang in ("pt", "en"):
        print(f"[{lang}] parse stats: {diag['parse_stats'][lang]}")
        for s in diag["skipped"][lang]:
            if s["reason"] not in ("not_released",):
                print(f"   skipped [{s['reason']}] table={s['table']} {s['heading']!r}: {s['text']}")
    print(f"reconcile: {diag['reconcile_stats']}")
    problems = validate_polls(merged)
    print(f"validate_polls: {problems or 'OK'}")
    np_ = merged["poll_id"].nunique()
    gap_n = merged.loc[merged["notes"].str.contains("gap_fill_en", na=False), "poll_id"].nunique()
    print(
        f"rows={len(merged)} polls={np_} pollsters={merged['pollster'].nunique()} "
        f"field_end {merged['field_end'].min()}..{merged['field_end'].max()} en_gap_fill_polls={gap_n} "
        f"conflict_rows={len(conflicts)} (share={int((conflicts['kind'] == 'share').sum())})"
    )


def main(pt_oldid: int = PT_OLDID, en_oldid: int = EN_OLDID) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Write the first-round and runoff head-to-head tables; returns the first-round (polls, conflicts)."""
    pt_html, pt_rec = fetch_revision("pt", PT_TITLE, pt_oldid)
    en_html, en_rec = fetch_revision("en", EN_TITLE, en_oldid)
    merged, conflicts, diag = build_tables(pt_html, pt_rec, en_html, en_rec, round_=1)
    runoff, runoff_conflicts, diag2 = build_tables(pt_html, pt_rec, en_html, en_rec, round_=2)

    OUT_POLLS.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(OUT_POLLS, index=False, encoding="utf-8")
    conflicts.to_csv(OUT_CONFLICTS, index=False, encoding="utf-8")
    runoff.to_csv(OUT_RUNOFF_POLLS, index=False, encoding="utf-8")
    runoff_conflicts.to_csv(OUT_RUNOFF_CONFLICTS, index=False, encoding="utf-8")

    _report(merged, conflicts, diag, "first round")
    summarize(merged)
    _report(runoff, runoff_conflicts, diag2, "runoff head-to-head (round 2)")
    summarize_runoff(runoff)
    print(f"\nwrote {_rel(OUT_POLLS)}, {_rel(OUT_CONFLICTS)}, {_rel(OUT_RUNOFF_POLLS)}, {_rel(OUT_RUNOFF_CONFLICTS)}")
    return merged, conflicts


if __name__ == "__main__":
    main()

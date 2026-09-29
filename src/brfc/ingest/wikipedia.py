"""Fetch Wikipedia pages pinned to an exact revision (oldid) via the MediaWiki API.

Raw HTML is cached under data/raw/wikipedia/ (not redistributed) with a provenance
record next to it, so every parsed poll row can cite page, revision and hash.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import requests

from brfc.provenance import SourceRecord, read_record, sha256_bytes, utc_now_iso, write_record

USER_AGENT = "brazil-2026-forecast/0.1 (research project; https://github.com/RafaelBraga-Kribitz)"
RAW_DIR = Path(__file__).resolve().parents[3] / "data" / "raw" / "wikipedia"


def _api(lang: str) -> str:
    return f"https://{lang}.wikipedia.org/w/api.php"


def latest_revision(lang: str, title: str) -> int:
    r = requests.get(
        _api(lang),
        params={
            "action": "query",
            "titles": title,
            "prop": "revisions",
            "rvprop": "ids",
            "format": "json",
            "formatversion": 2,
            "redirects": 1,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    r.raise_for_status()
    page = r.json()["query"]["pages"][0]
    return int(page["revisions"][0]["revid"])


def revision_sha1(lang: str, oldid: int) -> dict:
    """Stable identity of a revision: MediaWiki's SHA-1 of the revision wikitext, plus size and timestamp.

    The rendered HTML of a fixed revision is not byte-stable (MediaWiki re-renders pages as templates and the parser
    change), so the HTML SHA-256 is informational; the wikitext SHA-1 never changes for an oldid."""
    r = requests.get(
        _api(lang),
        params={
            "action": "query",
            "revids": oldid,
            "prop": "revisions",
            "rvprop": "ids|sha1|size|timestamp",
            "format": "json",
            "formatversion": 2,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    r.raise_for_status()
    rev = r.json()["query"]["pages"][0]["revisions"][0]
    return {"revision_sha1": rev["sha1"], "revision_size": int(rev["size"]), "revision_timestamp": rev["timestamp"]}


def revision_at(lang: str, title: str, timestamp_utc: str) -> tuple[int, str]:
    """Latest revision id at or before `timestamp_utc` (ISO, e.g. 2026-10-04T01:00:00Z) and its timestamp."""
    r = requests.get(
        _api(lang),
        params={
            "action": "query",
            "titles": title,
            "prop": "revisions",
            "rvprop": "ids|timestamp",
            "rvlimit": 1,
            "rvstart": timestamp_utc,
            "rvdir": "older",
            "format": "json",
            "formatversion": 2,
            "redirects": 1,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    r.raise_for_status()
    rev = r.json()["query"]["pages"][0]["revisions"][0]
    return int(rev["revid"]), rev["timestamp"]


def fetch_revision(
    lang: str, title: str, oldid: int | None = None, raw_dir: Path = RAW_DIR
) -> tuple[str, SourceRecord]:
    """Return (html, provenance) for `title` at revision `oldid` (latest if None). Cached on disk."""
    if oldid is None:
        oldid = latest_revision(lang, title)
    raw_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{lang}_{oldid}"
    html_path = raw_dir / f"{stem}.html"
    rec_path = raw_dir / f"{stem}.provenance.json"
    if html_path.exists() and rec_path.exists():
        return html_path.read_text(encoding="utf-8"), read_record(rec_path)

    r = requests.get(
        _api(lang),
        params={
            "action": "parse",
            "oldid": oldid,
            "prop": "text|revid|displaytitle",
            "format": "json",
            "formatversion": 2,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=120,
    )
    r.raise_for_status()
    payload = r.json()
    if "error" in payload:
        raise RuntimeError(json.dumps(payload["error"]))
    html = payload["parse"]["text"]
    html_path.write_text(html, encoding="utf-8")
    url = f"https://{lang}.wikipedia.org/w/index.php?title={quote(title.replace(' ', '_'))}&oldid={oldid}"
    rec = SourceRecord(
        source=f"wikipedia_{lang}",
        url=url,
        retrieval_timestamp=utc_now_iso(),
        source_type="wikipedia_revision",
        revision=str(oldid),
        sha256=sha256_bytes(html.encode("utf-8")),
        local_path=str(html_path.relative_to(raw_dir.parents[2])).replace("\\", "/"),
        description=title,
    )
    write_record(rec, rec_path)
    return html, rec

"""Provenance helpers: hashing and retrieval records for every external input."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def sha256_text_file_lf(path: str | Path) -> str:
    """SHA-256 of a text file with CRLF normalised to LF (stable across Windows and LF checkouts)."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class SourceRecord:
    """One retrieved external artefact (a page revision or a downloaded file)."""

    source: str  # e.g. "wikipedia_pt", "tse_dadosabertos"
    url: str
    retrieval_timestamp: str  # UTC ISO-8601
    source_type: str  # "wikipedia_revision" | "manual_download" | "pollster_release" | ...
    revision: str | None  # Wikipedia oldid or file version
    sha256: str
    local_path: str
    description: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


def write_record(record: SourceRecord, path: str | Path) -> None:
    Path(path).write_text(record.to_json() + "\n", encoding="utf-8")


def read_record(path: str | Path) -> SourceRecord:
    return SourceRecord(**json.loads(Path(path).read_text(encoding="utf-8")))

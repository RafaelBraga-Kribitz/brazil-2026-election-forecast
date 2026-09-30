"""Frozen-package utilities shared by the freeze scripts and the scorecard (PREREG s.11, Addendum 06 s.1).

No forecast logic and no result access: it writes and reads the draws file and checks a package's hash list.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from brfc.provenance import sha256_file

HASH_FILE = "forecast_hash.txt"
DRAWS_FILE = "draws.npz"


def save_draws(path: Path, draws: dict[str, np.ndarray], categories: list[str]) -> None:
    """Draws of each model (N, K) in category order, float64, in one compressed .npz."""
    arrays = {f"model_{m}": np.asarray(d, dtype=np.float64) for m, d in draws.items()}
    for m, a in arrays.items():
        if a.ndim != 2 or a.shape[1] != len(categories):
            raise ValueError(f"{m}: draws of shape {a.shape} do not match {len(categories)} categories")
    np.savez_compressed(path, categories=np.array(categories, dtype=str), **arrays)


def load_draws(path: Path) -> tuple[list[str], dict[str, np.ndarray]]:
    with np.load(path, allow_pickle=False) as z:
        cats = [str(c) for c in z["categories"]]
        draws = {k.removeprefix("model_"): z[k] for k in z.files if k.startswith("model_")}
    return cats, draws


def verify_package(fz: Path) -> list[str]:
    """Problems with a frozen package: hash file missing, a listed file missing or changed, or a file not listed."""
    fz = Path(fz)
    hf = fz / HASH_FILE
    if not hf.is_file():
        return [f"{hf} is missing"]
    problems, listed = [], set()
    for line in hf.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2:
            problems.append(f"malformed line in {HASH_FILE}: {line!r}")
            continue
        sha, name = parts
        listed.add(name)
        p = fz / name
        if not p.is_file():
            problems.append(f"{name}: listed but missing")
        elif sha256_file(p) != sha:
            problems.append(f"{name}: changed since it was hashed")
    present = {p.relative_to(fz).as_posix() for p in fz.rglob("*") if p.is_file()} - {HASH_FILE}
    problems += [f"{n}: in the package but not hashed" for n in sorted(present - listed)]
    return problems

"""Fixed design constants. Every value here is referenced in PREREG.md; change only via a dated addendum."""

from __future__ import annotations

from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
OUTPUTS = ROOT / "outputs"
FIGURES = ROOT / "figures"

# Election days (Sunday). Source: TSE electoral calendars.
ELECTION_DATES: dict[tuple[str, int], date] = {
    ("2014", 1): date(2014, 10, 5),
    ("2014", 2): date(2014, 10, 26),
    ("2018", 1): date(2018, 10, 7),
    ("2018", 2): date(2018, 10, 28),
    ("2022", 1): date(2022, 10, 2),
    ("2022", 2): date(2022, 10, 30),
    ("2026", 1): date(2026, 10, 4),
    ("2026", 2): date(2026, 10, 25),
}

HISTORICAL = ("2014", "2018", "2022")

# Registered presidential ballots (candidate registration is public before the forecast horizons used here;
# it is not an outcome). Canonical short names; must match the parsers' candidate labels.
BALLOTS: dict[str, list[str]] = {
    "2014": [
        "Dilma Rousseff", "Aécio Neves", "Marina Silva", "Luciana Genro", "Pastor Everaldo", "Eduardo Jorge",
        "Levy Fidelix", "Zé Maria", "José Maria Eymael", "Mauro Iasi", "Rui Costa Pimenta",
    ],
    "2018": [
        "Jair Bolsonaro", "Fernando Haddad", "Ciro Gomes", "Geraldo Alckmin", "João Amoêdo", "Cabo Daciolo",
        "Henrique Meirelles", "Marina Silva", "Alvaro Dias", "Guilherme Boulos", "Vera Lúcia", "José Maria Eymael",
        "João Goulart Filho",
    ],
    "2022": [
        "Lula", "Jair Bolsonaro", "Simone Tebet", "Ciro Gomes", "Soraya Thronicke", "Felipe d'Avila", "Padre Kelmon",
        "Léo Péricles", "Sofia Manzano", "Vera Lúcia", "José Maria Eymael",
    ],
    # 2026: filled from the TSE registered-candidate list before the freeze (see PREREG.md section 2).
    "2026": [],
}

# Runoff pairings: known once the first round is counted (information-fair for runoff forecasts).
RUNOFF_PAIRS: dict[str, tuple[str, str]] = {
    "2014": ("Dilma Rousseff", "Aécio Neves"),
    "2018": ("Jair Bolsonaro", "Fernando Haddad"),
    "2022": ("Lula", "Jair Bolsonaro"),
}

# Modelling window and horizons (days before election day).
WINDOW_DAYS_R1 = 60
HORIZONS_R1 = (30, 14, 7, 1)  # 1 == "eve": polls with fieldwork ending up to the day before the election
HORIZONS_R2 = (14, 7, 1)
MIN_POLLS_PER_FIT = 8  # a (election, round, horizon) fit is run only with at least this many polls

# Candidate grouping: modelled individually if mean valid share >= threshold over the last
# NAMED_LOOKBACK_DAYS before the cutoff; at most MAX_NAMED; the rest pooled as "others".
NAMED_THRESHOLD_PCT = 3.0
NAMED_LOOKBACK_DAYS = 14
MAX_NAMED = 5
OTHERS_LABEL = "Others"

# Volatility regime boundary (days before election day).
LATE_REGIME_DAYS = 10

# Interval levels (equal-tailed posterior predictive intervals).
INTERVALS = (0.80, 0.94)

# Sampler (PyMC NUTS, numba backend, chains run sequentially in one process).
SAMPLER = {"draws": 1000, "tune": 1000, "chains": 4, "target_accept": 0.95, "random_seed": 20261004}

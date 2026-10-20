"""The trading-session calendar both providers are measured against.

`GET /market/working-dates` is the official calendar, but it covers one year
**forward** from today (verified live: 258 dates from 2026-10-20 to 2027-10-20),
so it cannot describe a 12-month historical window.

The benchmark therefore derives the sessions from the data itself — a date on
which a symbol traded with volume above zero — and then *validates the method*
against the official calendar over the forward year, where the official one is
available. If the two disagree, the run is downgraded to INCONCLUSIVE rather
than reported as a pass.

Two details matter:

* The derivation takes the **union** over providers and symbols. Using the
  intersection would let a provider that dropped a session shrink the
  denominator, and the gap would vanish from the report.
* Yahoo emits a row for **every weekday**, holidays included, with
  `O=H=L=C=previous close` and `Volume = 0`. Those rows are not sessions, which
  is why volume is the discriminator.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .schema import CanonicalRow


@dataclass(frozen=True)
class Calendar:
    """The sessions of the window, and how they were established."""

    sessions: list[dt.date]
    #: The sessions the official endpoint lists, when it was available.
    official_sessions: list[dt.date] = field(default_factory=list)
    #: Whether the derived set was checked against the official one.
    validated: bool = False
    #: The agreement between the derived set and the official set.
    agreement: float | None = None
    #: Sessions only the derivation knows about, and only the official one.
    only_derived: list[dt.date] = field(default_factory=list)
    only_official: list[dt.date] = field(default_factory=list)
    #: Where the official calendar came from and what it covered.
    official_source: str = ""
    official_note: str = ""

    @property
    def session_set(self) -> set[dt.date]:
        return set(self.sessions)

    def as_dict(self) -> dict:
        return {
            "session_count": len(self.sessions),
            "first_session": self.sessions[0].isoformat() if self.sessions else None,
            "last_session": self.sessions[-1].isoformat() if self.sessions else None,
            "validated_against_official": self.validated,
            "agreement_with_official": self.agreement,
            "only_derived_count": len(self.only_derived),
            "only_official_count": len(self.only_official),
            "only_derived_sample": [d.isoformat() for d in self.only_derived[:20]],
            "only_official_sample": [d.isoformat() for d in self.only_official[:20]],
            "official_source": self.official_source,
            "official_note": self.official_note,
        }


def derive_sessions(rows: list[CanonicalRow]) -> set[dt.date]:
    """The dates on which something actually traded.

    A row with no volume is not a session: Yahoo's holiday fillers look like a
    bar but carry no trade.
    """
    sessions: set[dt.date] = set()
    for row in rows:
        volume = row.volume
        if volume is None or volume != volume:  # None or NaN
            continue
        if volume > 0:
            sessions.add(row.date)
    return sessions


def merge_sessions(*session_sets: set[dt.date]) -> list[dt.date]:
    """The union of session sets, in order."""
    merged: set[dt.date] = set()
    for sessions in session_sets:
        merged |= sessions
    return sorted(merged)


def neighbour_anchored_gaps(present: set[dt.date], sessions: list[dt.date]) -> list[dt.date]:
    """The sessions on which a ticker has no row, but should have had one.

    A ticker that was suspended, or that started or stopped trading during the
    window, legitimately has no row. A gap is only called a gap when the ticker
    traded on the session immediately before it and on the session immediately
    after it — that is, the market was open and the ticker was trading, so the
    row is missing rather than absent by design.

    The first and the last session of the window are never counted as gaps: the
    ticker may have started or stopped trading there.
    """
    if len(sessions) < 3:
        return []
    gaps: list[dt.date] = []
    for index in range(1, len(sessions) - 1):
        session = sessions[index]
        if session in present:
            continue
        previous = sessions[index - 1]
        following = sessions[index + 1]
        if previous in present and following in present:
            gaps.append(session)
    return gaps


def compare_to_official(derived: list[dt.date], official: list[dt.date]) -> dict:
    """How well the derived calendar agrees with the official one."""
    derived_set = set(derived)
    official_set = set(official)
    only_derived = sorted(derived_set - official_set)
    only_official = sorted(official_set - derived_set)
    if not official_set:
        return {
            "validated": False,
            "agreement": None,
            "only_derived": only_derived,
            "only_official": only_official,
        }
    shared = len(derived_set & official_set)
    denominator = len(derived_set | official_set)
    return {
        "validated": True,
        "agreement": (shared / denominator) if denominator else None,
        "only_derived": only_derived,
        "only_official": only_official,
    }

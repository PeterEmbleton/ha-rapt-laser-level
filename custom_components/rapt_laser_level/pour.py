"""Work out how much was actually poured.

The sensor switches to rapid readings (status 3) whenever it sees the
surface move, which also happens with no pour at all: foam settling after
the keg is gassed or moved, vibration, condensation. Single readings
during a burst wobble by several mm. So a pour is measured, not assumed:

- the baseline is the last calm reading (status 1 or 2) before a burst;
- the result is the first calm reading after the burst, or, if the sensor
  just goes quiet, the median of the burst's last few readings;
- it only counts if the level fell by at least POUR_MIN_L.

A level drop between two calm readings also counts, for when HA misses
the burst itself (weak Bluetooth signal).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from statistics import median

from .parser import LaserReading

RAPID = 3
_SETTLE_SAMPLES = 5


@dataclass
class Pour:
    litres: float
    at: datetime
    before_mm: float
    after_mm: float


@dataclass
class PourTracker:
    """Fed every new reading; returns a Pour when one has finished."""

    calm_mm: float | None = None
    burst: list[int] = field(default_factory=list)
    last_pour: Pour | None = None

    @property
    def in_burst(self) -> bool:
        return bool(self.burst)

    def reading(
        self, r: LaserReading, now: datetime, mm_to_l: float, min_l: float
    ) -> Pour | None:
        if r.status == RAPID:
            self.burst.append(r.distance_mm)
            return None
        if self.burst:
            return self._finish(r.distance_mm, now, mm_to_l, min_l)
        before, self.calm_mm = self.calm_mm, r.distance_mm
        if before is None:
            return None
        return self._maybe_pour(before, r.distance_mm, now, mm_to_l, min_l)

    def quiet(self, now: datetime, mm_to_l: float, min_l: float) -> Pour | None:
        """The sensor stopped sending mid-burst: settle on the last few readings."""
        if not self.burst:
            return None
        return self._finish(median(self.burst[-_SETTLE_SAMPLES:]), now, mm_to_l, min_l)

    def _finish(
        self, after_mm: float, now: datetime, mm_to_l: float, min_l: float
    ) -> Pour | None:
        before = self.calm_mm
        if before is None:
            before = median(self.burst[:3])
        self.burst = []
        self.calm_mm = after_mm
        return self._maybe_pour(before, after_mm, now, mm_to_l, min_l)

    def _maybe_pour(
        self, before_mm: float, after_mm: float, now: datetime, mm_to_l: float, min_l: float
    ) -> Pour | None:
        # Distance to the liquid grows as the keg empties.
        litres = (after_mm - before_mm) * mm_to_l
        if litres < min_l:
            return None
        self.last_pour = Pour(round(litres, 2), now, before_mm, after_mm)
        return self.last_pour

"""Decoder for RAPT Laser Level Sensor BLE adverts.

Layout (offsets into the manufacturer data after the 2-byte company id 0x4152),
from YDraig/rapt-laser-level-display docs/ble.md and BestEnemies/Rapt-Details:

  0-1   "PT" magic
  2     packet type (0xA1 seen for the laser)
  3-8   embedded sensor MAC
  9-10  distance, uint16 LE, mm from sensor to liquid
  11-12 remaining, uint16 LE, mm (sensor's own calculation)
  13    version
  14-15 battery, uint16 LE / 10 -> %
  16-17 temperature, int16 LE / 100 -> degC
  18    status bit field (3 seems to mean active/pouring, 1 idle; unconfirmed)

Placeholder packets sent right after power-on (battery 0xFFFF, temperature
0x8000) are ignored. Shorter "PT" packets (e.g. "PTQC" + 01) are identity/version frames and are
ignored.
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_LEN = 19


@dataclass(frozen=True)
class LaserReading:
    packet_type: int
    embedded_mac: str
    distance_mm: int | None
    remaining_mm: int | None
    version: int
    battery_pct: float | None
    temperature_c: float | None
    status: int
    raw: str


def decode(payload: bytes) -> LaserReading | None:
    """Decode a manufacturer-data payload, or return None if it is not a reading."""
    if len(payload) < MIN_LEN or payload[0:2] != b"PT":
        return None
    raw_battery = int.from_bytes(payload[14:16], "little")
    raw_temp = int.from_bytes(payload[16:18], "little", signed=True)
    if raw_battery == 0xFFFF and raw_temp == -0x8000:
        # Sent just after power-on, before the first measurement: distance
        # and remaining are 0 and battery/temperature hold "no value"
        # markers. Ignore it so it doesn't read as a full keg.
        return None
    distance = int.from_bytes(payload[9:11], "little")
    remaining = int.from_bytes(payload[11:13], "little")
    battery = int.from_bytes(payload[14:16], "little") / 10
    temp = int.from_bytes(payload[16:18], "little", signed=True) / 100
    return LaserReading(
        packet_type=payload[2],
        embedded_mac=":".join(f"{b:02X}" for b in payload[3:9]),
        distance_mm=None if distance in (0xFFFF,) else distance,
        remaining_mm=None if remaining in (0xFFFF,) else remaining,
        version=payload[13],
        battery_pct=battery if 0 <= battery <= 100 else None,
        temperature_c=temp if -40 <= temp <= 85 else None,
        status=payload[18],
        raw=payload.hex(),
    )


def level_percent(distance_mm: float, empty_mm: float, full_mm: float) -> float:
    """Percent full for a straight-sided vessel, clamped to 0-100."""
    span = empty_mm - full_mm
    if span <= 0:
        return 0.0
    pct = 100.0 * (empty_mm - distance_mm) / span
    return max(0.0, min(100.0, pct))

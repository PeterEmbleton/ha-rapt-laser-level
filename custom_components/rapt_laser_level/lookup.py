"""Background drink-picture lookup: retries with backoff, then a monthly check.

One schedule per keg with a product URL. Each run makes a single lookup
(at most two requests, see drink.async_lookup_once). Nothing is fetched
in between: the next run is a timer, saved to HA's storage so it survives
restarts instead of firing again on every start.

- No picture yet: try now, then back off 15 min, 1 h, 3 h, 6 h, 12 h,
  1, 2, 4, 8 and 16 days, then every 30 days (longer if the shop asks).
- Picture found: check again every 30 days, in case the shop changed it.
- A new picture or name is saved to the entry, which reloads the keg.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import random
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import CACHE_KEY, DOMAIN, LOOKUP_REFRESH_S, LOOKUP_RETRY_DELAYS
from .drink import async_lookup_once

_LOGGER = logging.getLogger(__name__)

_STORE_VERSION = 1
_STORE_KEY = f"{DOMAIN}.drink_lookups"
_DATA_STORE = "lookup_store"


async def _async_store(hass: HomeAssistant) -> tuple[Store, dict[str, Any]]:
    """Shared store of schedules, keyed by config entry id."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if _DATA_STORE not in domain_data:
        domain_data[_DATA_STORE] = asyncio.get_running_loop().create_future()
        store: Store = Store(hass, _STORE_VERSION, _STORE_KEY)
        data = await store.async_load() or {}
        domain_data[_DATA_STORE].set_result((store, data))
    return await asyncio.shield(domain_data[_DATA_STORE])


async def async_forget(hass: HomeAssistant, entry_id: str) -> None:
    """Drop a deleted keg's schedule."""
    store, data = await _async_store(hass)
    if data.pop(entry_id, None) is not None:
        await store.async_save(data)


def _monthly() -> timedelta:
    """A month, plus up to a day so kegs (and installs) don't line up."""
    return timedelta(seconds=LOOKUP_REFRESH_S + random.uniform(0, 86400))


def retry_delay(attempt: int, retry_after: float | None = None) -> float:
    """Seconds to wait after failed attempt number `attempt` (1-based)."""
    delay = (
        LOOKUP_RETRY_DELAYS[attempt - 1]
        if attempt <= len(LOOKUP_RETRY_DELAYS)
        else LOOKUP_REFRESH_S
    )
    return float(max(delay, min(retry_after or 0, LOOKUP_REFRESH_S)))


class DrinkLookup:
    """Keeps one keg's drink picture up to date."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, url: str) -> None:
        self.hass = hass
        self.entry = entry
        self.url = url
        self._unsub: CALLBACK_TYPE | None = None

    @property
    def _cached(self) -> dict[str, Any] | None:
        cached = self.entry.data.get(CACHE_KEY) or {}
        if cached.get("url") == self.url and cached.get("image"):
            return cached
        return None

    async def async_start(self) -> None:
        _, data = await _async_store(self.hass)
        rec = data.get(self.entry.entry_id) or {}
        now = dt_util.utcnow()
        if rec.get("url") == self.url and rec.get("next"):
            when = dt_util.parse_datetime(rec["next"]) or now
            self._publish(rec)
        elif self._cached:
            # Upgraded from a version without a schedule: check in a month.
            when = now + _monthly()
            await self._async_save(0, when)
        else:
            when = now
        if when <= now:
            self._run_soon()
        else:
            self._unsub = async_track_point_in_utc_time(self.hass, self._fire, when)

    @callback
    def async_cancel(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    @callback
    def _fire(self, _now: datetime) -> None:
        self._unsub = None
        self._run_soon()

    @callback
    def _run_soon(self) -> None:
        self.entry.async_create_background_task(
            self.hass, self._async_run(), f"{DOMAIN} drink lookup {self.entry.title}"
        )

    async def _async_save(
        self,
        attempt: int,
        when: datetime,
        last: datetime | None = None,
        result: str | None = None,
    ) -> None:
        store, data = await _async_store(self.hass)
        old = data.get(self.entry.entry_id) or {}
        if old.get("url") != self.url:
            old = {}
        rec = {
            "url": self.url,
            "attempt": attempt,
            "next": when.isoformat(),
            "last": last.isoformat() if last else old.get("last"),
            "result": result or old.get("result"),
        }
        data[self.entry.entry_id] = rec
        await store.async_save(data)
        self._publish(rec)

    @callback
    def _publish(self, rec: dict[str, Any]) -> None:
        """Show the schedule on the keg's Drink sensor."""
        state = getattr(self.entry, "runtime_data", None)
        if state is None:
            return
        state.lookup = {
            "lookup_last_attempt": rec.get("last"),
            "lookup_last_result": rec.get("result"),
            "lookup_failed_attempts": rec.get("attempt", 0),
            "lookup_next_attempt": rec.get("next"),
        }
        state.async_notify()

    async def _async_run(self) -> None:
        _, data = await _async_store(self.hass)
        rec = data.get(self.entry.entry_id) or {}
        attempt = rec.get("attempt", 0) if rec.get("url") == self.url else 0
        cached = self._cached

        res = await async_lookup_once(self.hass, self.url)
        now = dt_util.utcnow()

        if res.image:
            when = now + _monthly()
            await self._async_save(0, when, now, "found")
            if cached and (cached["image"], cached.get("title")) == (res.image, res.title):
                _LOGGER.debug("Drink picture for %s unchanged", self.url)
                self._unsub = async_track_point_in_utc_time(self.hass, self._fire, when)
                return
            _LOGGER.debug("Drink lookup for %s found %s", self.url, res.image)
            # Saving reloads the keg, which starts a fresh schedule.
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={
                    **self.entry.data,
                    CACHE_KEY: {"url": self.url, "image": res.image, "title": res.title},
                },
            )
            return

        if cached:
            # Keep the picture we have; just check again next month.
            wait = retry_delay(len(LOOKUP_RETRY_DELAYS) + 1, res.retry_after)
            attempt = 0
        else:
            attempt += 1
            wait = retry_delay(attempt, res.retry_after)
        when = now + timedelta(seconds=wait)
        await self._async_save(attempt, when, now, res.error or "no picture found")
        log = _LOGGER.warning if attempt in (1, len(LOOKUP_RETRY_DELAYS) + 1) else _LOGGER.info
        log(
            "Couldn't get a picture from %s (%s); trying again %s. You can paste "
            "an image address into the keg's Image URL option instead",
            self.url,
            res.error,
            _describe(wait),
        )
        self._unsub = async_track_point_in_utc_time(self.hass, self._fire, when)


def _describe(seconds: float) -> str:
    if seconds < 3600:
        return f"in {round(seconds / 60)} min"
    if seconds < 2 * 86400:
        return f"in {round(seconds / 3600)} h"
    return f"in {round(seconds / 86400)} days"

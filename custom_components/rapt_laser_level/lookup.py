"""Drink-picture lookup from Home Assistant: one polite attempt per link.

When a keg's product URL is set (or changed) and it has no picture, HA
asks the shop once (at most two requests, see drink.async_lookup_once).
There are no retries and no periodic checks. KegLand refuses HA's own
requests, so for KegLand the RAPT Keg card fills the picture in from the
viewer's browser instead (see websocket.py). The attempt is remembered in
HA's storage, so restarts and reloads don't ask again.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import CACHE_KEY, DOMAIN
from .drink import async_lookup_once

_LOGGER = logging.getLogger(__name__)

_STORE_VERSION = 1
_STORE_KEY = f"{DOMAIN}.drink_lookups"
_DATA_STORE = "lookup_store"


async def _async_store(hass: HomeAssistant) -> tuple[Store, dict[str, Any]]:
    """Shared record of attempts, keyed by config entry id."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if _DATA_STORE not in domain_data:
        domain_data[_DATA_STORE] = asyncio.get_running_loop().create_future()
        store: Store = Store(hass, _STORE_VERSION, _STORE_KEY)
        data = await store.async_load() or {}
        domain_data[_DATA_STORE].set_result((store, data))
    return await asyncio.shield(domain_data[_DATA_STORE])


async def async_forget(hass: HomeAssistant, entry_id: str) -> None:
    """Drop a deleted keg's record."""
    store, data = await _async_store(hass)
    if data.pop(entry_id, None) is not None:
        await store.async_save(data)


async def async_record(
    hass: HomeAssistant, entry: ConfigEntry, url: str, result: str
) -> None:
    """Remember an attempt for this link and show it on the Drink sensor."""
    store, data = await _async_store(hass)
    rec = {"url": url, "last": dt_util.utcnow().isoformat(), "result": result}
    data[entry.entry_id] = rec
    await store.async_save(data)
    publish(entry, rec)


@callback
def publish(entry: ConfigEntry, rec: dict[str, Any]) -> None:
    state = getattr(entry, "runtime_data", None)
    if state is None:
        return
    state.lookup = {
        "lookup_last_attempt": rec.get("last"),
        "lookup_last_result": rec.get("result"),
    }
    state.async_notify()


class DrinkLookup:
    """Looks one keg's drink picture up once per product URL."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, url: str) -> None:
        self.hass = hass
        self.entry = entry
        self.url = url

    async def async_start(self) -> None:
        _, data = await _async_store(self.hass)
        rec = data.get(self.entry.entry_id) or {}
        if rec.get("url") == self.url:
            publish(self.entry, rec)  # Already tried this link.
            return
        cached = self.entry.data.get(CACHE_KEY) or {}
        if cached.get("url") == self.url and cached.get("image"):
            return  # Found by an older version.
        self.entry.async_create_background_task(
            self.hass, self._async_run(), f"{DOMAIN} drink lookup {self.entry.title}"
        )

    async def _async_run(self) -> None:
        res = await async_lookup_once(self.hass, self.url)
        if not res.image:
            await async_record(self.hass, self.entry, self.url, res.error or "no picture found")
            _LOGGER.info(
                "Couldn't get a picture from %s (%s). The RAPT Keg card will try "
                "from your browser, or paste an image address into the keg's "
                "Image URL option",
                self.url,
                res.error,
            )
            return
        await async_record(self.hass, self.entry, self.url, "found")
        # Saving reloads the keg so its entities pick the picture up.
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={
                **self.entry.data,
                CACHE_KEY: {"url": self.url, "image": res.image, "title": res.title},
            },
        )

"""RAPT Laser Level Sensor, read locally from BLE adverts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from bluetooth_data_tools import monotonic_time_coarse

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DRINK,
    CONF_IMAGE_URL,
    CONF_PRODUCT_URL,
    CACHE_KEY,
    RAPT_MANUFACTURER_ID,
    STALE_AFTER_S,
)
from .drink import async_lookup
from .parser import LaserReading, decode

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.IMAGE]


@dataclass
class LaserState:
    """Latest data for one sensor, shared with its entities."""

    address: str
    reading: LaserReading | None = None
    rssi: int | None = None
    last_seen: datetime | None = None
    available: bool = False
    drink: str | None = None
    product_url: str | None = None
    image_url: str | None = None
    listeners: list[Callable[[], None]] = field(default_factory=list)

    @callback
    def async_add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self.listeners.append(cb)
        return lambda: self.listeners.remove(cb)

    @callback
    def async_notify(self) -> None:
        for cb in list(self.listeners):
            cb()


type RaptLaserConfigEntry = ConfigEntry[LaserState]


async def async_setup_entry(hass: HomeAssistant, entry: RaptLaserConfigEntry) -> bool:
    address: str = entry.data[CONF_ADDRESS].upper()
    state = LaserState(address=address)
    entry.runtime_data = state

    opts = entry.options
    state.drink = (opts.get(CONF_DRINK) or "").strip() or None
    state.product_url = (opts.get(CONF_PRODUCT_URL) or "").strip() or None
    state.image_url = (opts.get(CONF_IMAGE_URL) or "").strip() or None
    if state.product_url and (state.image_url is None or state.drink is None):
        cached = entry.data.get(CACHE_KEY) or {}
        if cached.get("url") == state.product_url and cached.get("image"):
            # Looked up before: don't hit the shop on every restart (KegLand
            # rate-limits with 429s).
            image, title = cached["image"], cached.get("title")
        else:
            image, title = await async_lookup(hass, state.product_url)
            if image:
                hass.config_entries.async_update_entry(
                    entry,
                    data={
                        **entry.data,
                        CACHE_KEY: {"url": state.product_url, "image": image, "title": title},
                    },
                )
        state.image_url = state.image_url or image
        state.drink = state.drink or title

    @callback
    def _on_advert(
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        payload = service_info.manufacturer_data.get(RAPT_MANUFACTURER_ID)
        state.rssi = service_info.rssi
        if payload is None:
            return
        reading = decode(payload)
        _LOGGER.debug(
            "%s advert %s -> %s", address, payload.hex(), reading
        )
        if reading is None:
            return
        state.reading = reading
        state.last_seen = dt_util.utcnow()
        state.available = True
        state.async_notify()

    @callback
    def _check_stale(_now: datetime) -> None:
        # HA drops adverts that are byte-identical to the previous one, so a
        # steady keg produces no callbacks. HA still records when it last
        # heard the device, so use that to refresh freshness.
        info = bluetooth.async_last_service_info(hass, address, connectable=False)
        if info is not None:
            payload = info.manufacturer_data.get(RAPT_MANUFACTURER_ID)
            if payload is not None and decode(payload) is not None:
                age = max(0.0, monotonic_time_coarse() - info.time)
                heard = dt_util.utcnow() - timedelta(seconds=age)
                if state.last_seen is None or heard > state.last_seen + timedelta(seconds=1):
                    state.last_seen = heard
                    state.available = True
                    state.async_notify()
        # The sensor also sends short identity frames, so judge freshness on
        # decodable readings only.
        if state.available and state.last_seen is not None and (
            dt_util.utcnow() - state.last_seen
        ).total_seconds() > STALE_AFTER_S:
            _LOGGER.debug("%s: no reading for %ss, marking unavailable", address, STALE_AFTER_S)
            state.available = False
            state.async_notify()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            _on_advert,
            bluetooth.BluetoothCallbackMatcher(address=address, connectable=False),
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
    )
    entry.async_on_unload(
        async_track_time_interval(hass, _check_stale, timedelta(seconds=60))
    )
    # Replay whatever HA has already cached for this address.
    if (info := bluetooth.async_last_service_info(hass, address, connectable=False)):
        _on_advert(info, bluetooth.BluetoothChange.ADVERTISEMENT)

    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: RaptLaserConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: RaptLaserConfigEntry) -> None:
    """Let HA offer the sensor for discovery again once its entry is deleted."""
    bluetooth.async_rediscover_address(hass, entry.data[CONF_ADDRESS].upper())


async def async_unload_entry(hass: HomeAssistant, entry: RaptLaserConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

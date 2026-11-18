"""RAPT Laser Level Sensor, read locally from BLE adverts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import Any
from pathlib import Path
from bluetooth_data_tools import monotonic_time_coarse

from homeassistant.components import bluetooth
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DRINK,
    CONF_IMAGE_URL,
    CONF_PRODUCT_URL,
    CACHE_KEY,
    DOMAIN,
    POUR_ENDED_AFTER_S,
    RAPT_MANUFACTURER_ID,
    STALE_AFTER_S,
)
from .drink import clean_product_url, tidy_title
from . import websocket
from .lookup import DrinkLookup, async_forget
from .parser import LaserReading, decode

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.IMAGE]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_DATA_LAST = "last_state"  # address -> LaserState, kept across reloads

CARD_URL = f"/{DOMAIN}/rapt-keg-card.js"
CARD_FILE = Path(__file__).parent / "frontend" / "rapt-keg-card.js"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the RAPT Keg dashboard card and load it on every dashboard."""
    websocket.async_setup(hass)
    # Frontend loads first when it's configured (after_dependencies).
    if hass.http is not None and "frontend" in hass.config.components:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL, str(CARD_FILE), True)]
        )
        version = (await async_get_integration(hass, DOMAIN)).version
        # The version query makes browsers fetch the new card after an update.
        add_extra_js_url(hass, f"{CARD_URL}?v={version}")
    return True


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
    # Monotonic time of the last new (changed) reading. HA drops identical
    # adverts, so this only moves when the sensor reports something new.
    last_change: float | None = None
    cancel_pour_timer: Callable[[], None] | None = None
    # Drink-picture lookup schedule, shown on the Drink sensor.
    lookup: dict[str, Any] | None = None
    listeners: list[Callable[[], None]] = field(default_factory=list)

    @callback
    def async_add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self.listeners.append(cb)
        return lambda: self.listeners.remove(cb)

    @property
    def status(self) -> int | None:
        """Sensor status, with a stale "rapid readings" treated as idle (1).

        After a pour the sensor stops sending, but HA keeps its last packet,
        which still says 3. No new reading for POUR_ENDED_AFTER_S means the
        pour is over.
        """
        if self.reading is None:
            return None
        if (
            self.reading.status == 3
            and self.last_change is not None
            and monotonic_time_coarse() - self.last_change > POUR_ENDED_AFTER_S
        ):
            return 1
        return self.reading.status

    @callback
    def async_notify(self) -> None:
        for cb in list(self.listeners):
            cb()


type RaptLaserConfigEntry = ConfigEntry[LaserState]


async def async_setup_entry(hass: HomeAssistant, entry: RaptLaserConfigEntry) -> bool:
    address: str = entry.data[CONF_ADDRESS].upper()
    state = LaserState(address=address)
    entry.runtime_data = state
    # Keep the last reading across a reload (e.g. saving the keg's options),
    # so the sensors don't go blank until the next hourly report.
    prev: LaserState | None = hass.data.get(DOMAIN, {}).get(_DATA_LAST, {}).pop(address, None)
    if prev is not None and prev.reading is not None and prev.last_seen is not None and (
        dt_util.utcnow() - prev.last_seen
    ).total_seconds() < STALE_AFTER_S:
        state.reading = prev.reading
        state.last_seen = prev.last_seen
        state.last_change = prev.last_change
        state.rssi = prev.rssi
        state.available = True

    opts = entry.options
    state.drink = (opts.get(CONF_DRINK) or "").strip() or None
    state.product_url = (
        clean_product_url((opts.get(CONF_PRODUCT_URL) or "").strip()) or None
    )
    # Older versions saved pasted addresses as typed, e.g. JSON-escaped "\/".
    state.image_url = (opts.get(CONF_IMAGE_URL) or "").strip().replace("\\/", "/") or None
    needs_lookup = False
    if state.product_url and (state.image_url is None or state.drink is None):
        cached = entry.data.get(CACHE_KEY) or {}
        if cached.get("url") == state.product_url and cached.get("image"):
            # Looked up before: don't hit the shop on every restart (KegLand
            # rate-limits with 429s).
            state.image_url = state.image_url or cached["image"]
            state.drink = state.drink or tidy_title(cached.get("title"))
        needs_lookup = True

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
        state.last_change = monotonic_time_coarse()
        state.available = True
        if state.cancel_pour_timer is not None:
            state.cancel_pour_timer()
            state.cancel_pour_timer = None
        if reading.status == 3:
            # Re-check once the pour has had time to go quiet.
            state.cancel_pour_timer = async_call_later(
                hass, POUR_ENDED_AFTER_S + 1, _pour_check
            )
        state.async_notify()

    @callback
    def _pour_check(_now: datetime) -> None:
        state.cancel_pour_timer = None
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

    @callback
    def _cancel_pour_timer() -> None:
        if state.cancel_pour_timer is not None:
            state.cancel_pour_timer()
            state.cancel_pour_timer = None

    entry.async_on_unload(_cancel_pour_timer)
    if needs_lookup:
        await DrinkLookup(hass, entry, state.product_url).async_start()
    return True


async def _async_reload(hass: HomeAssistant, entry: RaptLaserConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: RaptLaserConfigEntry) -> None:
    """Let HA offer the sensor for discovery again once its entry is deleted."""
    bluetooth.async_rediscover_address(hass, entry.data[CONF_ADDRESS].upper())
    await async_forget(hass, entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: RaptLaserConfigEntry) -> bool:
    if ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        state = entry.runtime_data
        hass.data.setdefault(DOMAIN, {}).setdefault(_DATA_LAST, {})[state.address] = state
    return ok

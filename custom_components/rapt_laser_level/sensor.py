"""Sensors for RAPT Laser Level (local BLE)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    RestoreSensor,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfLength,
    UnitOfTemperature,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LaserState, RaptLaserConfigEntry
from .const import (
    CONF_CAPACITY_L,
    CONF_EMPTY_MM,
    CONF_FULL_MM,
    DEFAULT_CAPACITY_L,
    DEFAULT_EMPTY_MM,
    DEFAULT_FULL_MM,
    DOMAIN,
    MANUFACTURER,
    MODEL,
)
from .parser import level_percent

# Status byte meanings are not documented by RAPT. These come from community
# notes (YDraig/rapt-laser-level-display) and our own captures; 1 is inferred.
STATUS_LABELS = {
    1: "Idle",
    2: "Single reading",
    3: "Rapid readings (pour)",
}
STATUS_UNKNOWN = "Unknown"


def _status_label(state: LaserState, opts: dict[str, Any]) -> str:
    return STATUS_LABELS.get(state.status, STATUS_UNKNOWN)


@dataclass(frozen=True, kw_only=True)
class LaserSensorDescription(SensorEntityDescription):
    value_fn: Callable[[LaserState, dict[str, Any]], Any]
    needs_reading: bool = True


def _pct(state: LaserState, opts: dict[str, Any]) -> float | None:
    r = state.reading
    if r is None or r.distance_mm is None:
        return None
    return round(
        level_percent(
            r.distance_mm,
            opts.get(CONF_EMPTY_MM, DEFAULT_EMPTY_MM),
            opts.get(CONF_FULL_MM, DEFAULT_FULL_MM),
        ),
        1,
    )


def _litres(state: LaserState, opts: dict[str, Any]) -> float | None:
    pct = _pct(state, opts)
    if pct is None:
        return None
    return round(opts.get(CONF_CAPACITY_L, DEFAULT_CAPACITY_L) * pct / 100, 2)


DESCRIPTIONS: tuple[LaserSensorDescription, ...] = (
    LaserSensorDescription(
        key="level",
        translation_key="level",
        name="Level",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:keg",
        value_fn=_pct,
    ),
    LaserSensorDescription(
        key="volume",
        translation_key="volume",
        name="Volume",
        device_class=SensorDeviceClass.VOLUME_STORAGE,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_litres,
    ),
    LaserSensorDescription(
        key="distance",
        translation_key="distance",
        name="Distance",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s, o: s.reading.distance_mm,
    ),
    LaserSensorDescription(
        key="remaining",
        translation_key="remaining",
        name="Remaining height (sensor)",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s, o: s.reading.remaining_mm,
    ),
    LaserSensorDescription(
        key="temperature",
        translation_key="temperature",
        name="Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda s, o: s.reading.temperature_c,
    ),
    LaserSensorDescription(
        key="battery",
        translation_key="battery",
        name="Battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s, o: s.reading.battery_pct,
    ),
    LaserSensorDescription(
        key="activity",
        translation_key="activity",
        name="Activity",
        device_class=SensorDeviceClass.ENUM,
        options=[*STATUS_LABELS.values(), STATUS_UNKNOWN],
        icon="mdi:beer-outline",
        value_fn=_status_label,
    ),
    LaserSensorDescription(
        key="status",
        translation_key="status",
        name="Status code",
        icon="mdi:information-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s, o: s.reading.status,
    ),
    LaserSensorDescription(
        key="last_reading",
        translation_key="last_reading",
        name="Last reading",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s, o: s.last_seen,
    ),
    LaserSensorDescription(
        key="rssi",
        translation_key="rssi",
        name="Signal strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s, o: s.rssi,
        needs_reading=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RaptLaserConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(
        [*(LaserSensor(entry, d) for d in DESCRIPTIONS), DrinkSensor(entry), LastPourSensor(entry)]
    )


def keg_device_info(entry: RaptLaserConfigEntry) -> DeviceInfo:
    addr = entry.runtime_data.address
    return DeviceInfo(
        identifiers={(DOMAIN, addr)},
        connections={(CONNECTION_BLUETOOTH, addr)},
        manufacturer=MANUFACTURER,
        model=MODEL,
        name=entry.title,
    )


class DrinkSensor(SensorEntity):
    """What's in the keg. Set in the keg's Configure options."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Drink"
    _attr_translation_key = "drink"
    _attr_icon = "mdi:glass-mug-variant"

    def __init__(self, entry: RaptLaserConfigEntry) -> None:
        st = entry.runtime_data
        self._attr_unique_id = f"{st.address}_drink"
        self._attr_device_info = keg_device_info(entry)
        self._state = st
        self._attr_native_value = st.drink
        self._attr_entity_picture = st.image_url

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        st = self._state
        return {
            "product_url": st.product_url,
            "image_url": st.image_url,
            **{k: v for k, v in (st.lookup or {}).items() if v is not None},
        }

    async def async_added_to_hass(self) -> None:
        self._last_lookup = self._state.lookup
        self.async_on_remove(self._state.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        # Readings also notify; only rewrite when the lookup status changed.
        if self._state.lookup != self._last_lookup:
            self._last_lookup = self._state.lookup
            self.async_write_ha_state()


class LaserSensor(SensorEntity):
    """One value from a RAPT laser level sensor."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    entity_description: LaserSensorDescription

    def __init__(self, entry: RaptLaserConfigEntry, description: LaserSensorDescription) -> None:
        self.entity_description = description
        self._entry = entry
        self._state: LaserState = entry.runtime_data
        addr = self._state.address
        self._attr_unique_id = f"{addr}_{description.key}"
        self._attr_device_info = keg_device_info(entry)

    @property
    def available(self) -> bool:
        if not self.entity_description.needs_reading:
            return self._state.rssi is not None
        return self._state.available and self._state.reading is not None

    @property
    def native_value(self) -> Any:
        if self.entity_description.needs_reading and self._state.reading is None:
            return None
        return self.entity_description.value_fn(self._state, dict(self._entry.options))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        r = self._state.reading
        if self.entity_description.key != "status" or r is None:
            return None
        return {
            "raw_payload": r.raw,
            "packet_type": f"0x{r.packet_type:02X}",
            "version": r.version,
            "embedded_mac": r.embedded_mac,
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._state.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class LastPourSensor(RestoreSensor):
    """Litres in the most recent measured pour (see pour.py)."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Last pour"
    _attr_translation_key = "last_pour"
    _attr_icon = "mdi:beer"
    _attr_device_class = SensorDeviceClass.VOLUME
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_suggested_display_precision = 2

    def __init__(self, entry: RaptLaserConfigEntry) -> None:
        self._state = entry.runtime_data
        self._attr_unique_id = f"{self._state.address}_last_pour"
        self._attr_device_info = keg_device_info(entry)
        self._shown = None

    async def async_added_to_hass(self) -> None:
        if (pour := self._state.pour.last_pour) is not None:
            self._show(pour)
        elif (last := await self.async_get_last_sensor_data()) is not None:
            self._attr_native_value = last.native_value
            if (old := await self.async_get_last_state()) is not None:
                self._attr_extra_state_attributes = {
                    k: v for k, v in old.attributes.items() if k == "poured_at"
                }
        self.async_on_remove(self._state.async_add_listener(self._handle_update))

    def _show(self, pour) -> None:
        self._shown = pour
        self._attr_native_value = pour.litres
        self._attr_extra_state_attributes = {"poured_at": pour.at.isoformat()}

    @callback
    def _handle_update(self) -> None:
        pour = self._state.pour.last_pour
        if pour is not None and pour is not self._shown:
            self._show(pour)
            self.async_write_ha_state()

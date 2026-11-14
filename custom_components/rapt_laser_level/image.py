"""Drink picture for a keg, for use on dashboards."""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import RaptLaserConfigEntry
from .sensor import keg_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RaptLaserConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    if entry.runtime_data.image_url:
        async_add_entities([DrinkImage(hass, entry)])


class DrinkImage(ImageEntity):
    """Picture of the drink in this keg."""

    _attr_has_entity_name = True
    _attr_name = "Drink image"

    def __init__(self, hass: HomeAssistant, entry: RaptLaserConfigEntry) -> None:
        super().__init__(hass)
        st = entry.runtime_data
        self._attr_unique_id = f"{st.address}_drink_image"
        self._attr_device_info = keg_device_info(entry)
        self._attr_image_url = st.image_url
        self._attr_image_last_updated = dt_util.utcnow()

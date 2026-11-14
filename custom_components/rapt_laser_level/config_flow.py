"""Config flow for RAPT Laser Level (local BLE)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback

from .const import (
    CONF_CAPACITY_L,
    CONF_EMPTY_MM,
    CONF_DRINK,
    CONF_FULL_MM,
    CONF_IMAGE_URL,
    CONF_KEG_NAME,
    CONF_PRODUCT_URL,
    DEFAULT_CAPACITY_L,
    DEFAULT_EMPTY_MM,
    DEFAULT_FULL_MM,
    DOMAIN,
    RAPT_MANUFACTURER_ID,
)


def _is_rapt_laser(info: BluetoothServiceInfoBleak) -> bool:
    """Laser sensors send "PT"+0xA1 readings and "PTQC" identity frames.

    RAPT Pills share the manufacturer id but use "PT"+0x01/0x02, so they
    are not offered here.
    """
    name = info.name or ""
    if name.startswith("RAPT Laser"):
        return True
    data = info.manufacturer_data.get(RAPT_MANUFACTURER_ID)
    if not data or data[:2] != b"PT":
        return False
    return (len(data) >= 19 and data[2] == 0xA1) or data[:4] == b"PTQC"


def _normalise_url(value: Any) -> str | None:
    """Return a cleaned-up URL ("" for blank), or None if it isn't one."""
    value = str(value or "").strip()
    if not value:
        return ""
    if not value.lower().startswith(("http://", "https://")):
        value = "https://" + value
    if " " in value or "." not in value.split("//", 1)[1].split("/", 1)[0]:
        return None
    return value


def _clean_input(user_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Normalise URL fields; return (data, errors)."""
    data = dict(user_input)
    errors: dict[str, str] = {}
    for key in (CONF_PRODUCT_URL, CONF_IMAGE_URL):
        url = _normalise_url(data.get(key))
        if url is None:
            errors[key] = "invalid_url"
        else:
            data[key] = url
    return data, errors


def _keg_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_KEG_NAME, default=defaults.get(CONF_KEG_NAME, "")): str,
            vol.Optional(
                CONF_DRINK, description={"suggested_value": defaults.get(CONF_DRINK)}
            ): str,
            vol.Optional(
                CONF_PRODUCT_URL,
                description={"suggested_value": defaults.get(CONF_PRODUCT_URL)},
            ): str,
            vol.Optional(
                CONF_IMAGE_URL,
                description={"suggested_value": defaults.get(CONF_IMAGE_URL)},
            ): str,
            vol.Required(
                CONF_CAPACITY_L, default=defaults.get(CONF_CAPACITY_L, DEFAULT_CAPACITY_L)
            ): vol.All(vol.Coerce(float), vol.Range(min=1, max=100)),
            vol.Required(
                CONF_EMPTY_MM, default=defaults.get(CONF_EMPTY_MM, DEFAULT_EMPTY_MM)
            ): vol.All(vol.Coerce(int), vol.Range(min=50, max=2000)),
            vol.Required(
                CONF_FULL_MM, default=defaults.get(CONF_FULL_MM, DEFAULT_FULL_MM)
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=500)),
        }
    )


class RaptLaserConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add one RAPT laser level sensor per entry."""

    VERSION = 1

    def __init__(self) -> None:
        self._address: str | None = None
        self._name: str | None = None
        self._found: dict[str, str] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        if not _is_rapt_laser(discovery_info):
            return self.async_abort(reason="not_supported")
        await self.async_set_unique_id(discovery_info.address.upper())
        self._abort_if_unique_id_configured()
        self._address = discovery_info.address.upper()
        self._name = discovery_info.name or "RAPT Laser Level"
        self.context["title_placeholders"] = {"name": f"{self._name} ({self._address})"}
        return await self.async_step_keg()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._address = user_input[CONF_ADDRESS].strip().upper()
            await self.async_set_unique_id(self._address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            self._name = self._found.get(self._address, "RAPT Laser Level")
            return await self.async_step_keg()

        configured = self._async_current_ids(include_ignore=False)
        for info in async_discovered_service_info(self.hass, connectable=False):
            addr = info.address.upper()
            if addr not in configured and _is_rapt_laser(info):
                self._found[addr] = f"{info.name or 'RAPT Laser'} ({addr}, {info.rssi} dBm)"
        if self._found:
            schema = vol.Schema({vol.Required(CONF_ADDRESS): vol.In(self._found)})
        else:
            schema = vol.Schema({vol.Required(CONF_ADDRESS): str})
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_keg(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._address is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = _clean_input(user_input)
            if not errors:
                title = data[CONF_KEG_NAME] or self._address
                return self.async_create_entry(
                    title=title,
                    data={CONF_ADDRESS: self._address},
                    options=data,
                )
        return self.async_show_form(
            step_id="keg",
            data_schema=_keg_schema(user_input or {}),
            errors=errors,
            description_placeholders={"address": self._address},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return RaptLaserOptionsFlow()


class RaptLaserOptionsFlow(OptionsFlow):
    """Change keg name and calibration."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = _clean_input(user_input)
            if not errors:
                return self.async_create_entry(data=data)
        return self.async_show_form(
            step_id="init",
            data_schema=_keg_schema(user_input or dict(self.config_entry.options)),
            errors=errors,
        )

"""Let the dashboard card hand over a drink picture found by the browser.

KegLand's shop refuses Home Assistant's own requests (bot protection) but
answers browsers, and allows pages on other sites to read its public
product feed. So the RAPT Keg card looks the picture up from the viewer's
browser and sends the result here to be saved, exactly as if the
integration had found it itself.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import CACHE_KEY, DOMAIN
from .drink import clean_product_url, tidy_title
from .lookup import async_record


@callback
def async_setup(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_drink_found)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/drink_found",
        vol.Required("entity_id"): str,
        vol.Required("product_url"): vol.All(str, vol.Length(max=2048)),
        vol.Required("image"): vol.All(str, vol.Length(max=2048)),
        vol.Optional("title"): vol.Any(None, vol.All(str, vol.Length(max=300))),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_drink_found(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    ent = er.async_get(hass).async_get(msg["entity_id"])
    entry = (
        hass.config_entries.async_get_entry(ent.config_entry_id)
        if ent and ent.platform == DOMAIN and ent.config_entry_id
        else None
    )
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Not a RAPT keg's Drink sensor")
        return
    url = clean_product_url(msg["product_url"].strip())
    state = getattr(entry, "runtime_data", None)
    if state is None or url != state.product_url:
        connection.send_error(msg["id"], "url_mismatch", "That isn't this keg's product URL")
        return
    image = msg["image"].strip()
    if image.startswith("//"):
        image = "https:" + image
    if not image.startswith("https://") or " " in image:
        connection.send_error(msg["id"], "invalid_image", "The picture must be an https address")
        return
    title = (msg.get("title") or "").strip() or None
    cached = entry.data.get(CACHE_KEY) or {}
    await async_record(hass, entry, url, "found by the dashboard card")
    if (cached.get("url"), cached.get("image"), cached.get("title")) != (url, image, title):
        # Saving reloads the keg so its entities pick the picture up.
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CACHE_KEY: {"url": url, "image": image, "title": title}},
        )
    connection.send_result(msg["id"], {"image": image, "title": tidy_title(title)})

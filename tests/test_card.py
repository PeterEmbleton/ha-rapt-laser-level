from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.rapt_laser_level import CARD_URL
from custom_components.rapt_laser_level.const import DOMAIN


async def test_card_is_served_and_registered(hass: HomeAssistant, hass_client):
    hass.config.components.add("frontend")
    with patch("custom_components.rapt_laser_level.add_extra_js_url") as add_js:
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()
    url = add_js.call_args.args[1]
    assert url.startswith(CARD_URL + "?v=")
    client = await hass_client()
    resp = await client.get(CARD_URL)
    assert resp.status == 200
    assert "customElements.define(\"rapt-keg-card\"" in await resp.text()


async def test_no_frontend_no_card(hass: HomeAssistant):
    with patch("custom_components.rapt_laser_level.add_extra_js_url") as add_js:
        assert await async_setup_component(hass, DOMAIN, {})
    add_js.assert_not_called()

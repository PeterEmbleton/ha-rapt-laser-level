from unittest.mock import patch, AsyncMock
from homeassistant.core import HomeAssistant
from custom_components.rapt_laser_level.drink import LookupResult
from homeassistant.const import CONF_ADDRESS
from pytest_homeassistant_custom_component.common import MockConfigEntry
from custom_components.rapt_laser_level.drink import parse_meta
from custom_components.rapt_laser_level.const import DOMAIN

ADDR = "EE:90:A8:0D:F5:38"
PAGE = '''<html><head>
<meta property="og:title" content="Diet Orange Stripe Brewed Ginger Beer - Jamaican - Ready To Keg Kit">
<meta content="http://kegland.com.au/cdn/shop/files/KB31873-OrangeStripeBrewedGingerBeer-Diet-Jamaican-RTK-01-Base-01.jpg?v=1743137780" property="og:image">
<meta name="twitter:title" content="x"></head></html>'''

def test_parse():
    img, title = parse_meta(PAGE, "https://kegland.com.au/products/x")
    assert img == "https://kegland.com.au/cdn/shop/files/KB31873-OrangeStripeBrewedGingerBeer-Diet-Jamaican-RTK-01-Base-01.jpg?v=1743137780"
    assert title == "Diet Orange Stripe Brewed Ginger Beer - Jamaican"
    assert parse_meta('<meta property="og:image" content="/a.jpg">', "https://x.com/p/1")[0] == "https://x.com/a.jpg"
    assert parse_meta("<html></html>", "https://x.com") == (None, None)

async def test_drink_entities(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=ADDR, title="Tap 3", data={CONF_ADDRESS: ADDR},
        options={"keg_name": "Tap 3", "drink_type": "Ginger Beer",
                 "product_url": "https://kegland.com.au/products/orange-stripe-brewed-ginger-beer-diet-jamaican-ready-to-keg-kit",
                 "capacity_l": 19.0, "empty_distance_mm": 560, "full_distance_mm": 26})
    entry.add_to_hass(hass)
    lookup = AsyncMock(return_value=LookupResult("https://img/x.jpg", "Diet Orange Stripe"))
    with patch("homeassistant.components.bluetooth.async_register_callback", return_value=lambda: None), \
         patch("homeassistant.components.bluetooth.async_last_service_info", return_value=None), \
         patch("custom_components.rapt_laser_level.lookup.async_lookup_once", lookup):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    d = hass.states.get("sensor.tap_3_drink")
    assert d.state == "Ginger Beer"  # user's name wins over page title
    assert d.attributes["entity_picture"] == "https://img/x.jpg"
    assert d.attributes["product_url"].startswith("https://kegland")
    img = hass.states.get("image.tap_3_drink_image")
    assert img is not None, [s.entity_id for s in hass.states.async_all()]
    lookup.assert_awaited_once()

async def test_options_url_without_scheme(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=ADDR, title="Tap 3", data={CONF_ADDRESS: ADDR},
        options={"keg_name": "Tap 3", "capacity_l": 19.0, "empty_distance_mm": 560, "full_distance_mm": 26})
    entry.add_to_hass(hass)
    with patch("custom_components.rapt_laser_level.async_setup_entry", return_value=True):
        await hass.config_entries.async_setup(entry.entry_id)
        r = await hass.config_entries.options.async_init(entry.entry_id)
        r = await hass.config_entries.options.async_configure(r["flow_id"], {
            "keg_name": "Tap 3", "drink_type": "Ginger Beer",
            "product_url": "kegland.com.au/products/orange-stripe", "image_url": "",
            "capacity_l": 19, "empty_distance_mm": 560, "full_distance_mm": 26})
        await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.options["product_url"] == "https://kegland.com.au/products/orange-stripe"

def test_schema_serializes():
    # The frontend must be able to serialise the form (this broke 0.2.0).
    import voluptuous_serialize
    from homeassistant.helpers import config_validation as cv
    from custom_components.rapt_laser_level.config_flow import _keg_schema
    for d in ({}, {"product_url": "https://a.b/c", "drink_type": "x"}):
        voluptuous_serialize.convert(_keg_schema(d), custom_serializer=cv.custom_serializer)
    try:
        import probatio.codecs.fields as pf
        pf.to_field_list(_keg_schema({}), custom_serializer=cv.custom_serializer)
    except ImportError:
        pass

async def test_options_bad_url(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=ADDR, title="Tap 3", data={CONF_ADDRESS: ADDR},
        options={"keg_name": "Tap 3", "capacity_l": 19.0, "empty_distance_mm": 560, "full_distance_mm": 26})
    entry.add_to_hass(hass)
    with patch("custom_components.rapt_laser_level.async_setup_entry", return_value=True):
        await hass.config_entries.async_setup(entry.entry_id)
        r = await hass.config_entries.options.async_init(entry.entry_id)
        r = await hass.config_entries.options.async_configure(r["flow_id"], {
            "keg_name": "Tap 3", "product_url": "not a url", "capacity_l": 19,
            "empty_distance_mm": 560, "full_distance_mm": 26})
    assert r["type"] == "form" and r["errors"] == {"product_url": "invalid_url"}

def test_shopify_js():
    from custom_components.rapt_laser_level.drink import parse_shopify_js
    img, t = parse_shopify_js({"title": "Ginger", "featured_image": "//kegland.com.au/cdn/shop/files/a.jpg?v=1"}, "https://kegland.com.au/products/x")
    assert img == "https://kegland.com.au/cdn/shop/files/a.jpg?v=1" and t == "Ginger"

def test_discovery_filter():
    from unittest.mock import MagicMock
    from custom_components.rapt_laser_level.config_flow import _is_rapt_laser
    def mk(name, data):
        m = MagicMock(); m.name = name; m.manufacturer_data = {16722: data}; return m
    assert _is_rapt_laser(mk(None, bytes.fromhex("5054514301")))
    assert _is_rapt_laser(mk("EE:90", bytes.fromhex("5054a1ee90a80df5384100ef010ae803ca0802")))
    assert not _is_rapt_laser(mk("RAPT Pill", b"PT\x02" + bytes(20)))


async def test_lookup_is_cached(hass: HomeAssistant):
    opts = {"keg_name": "Tap 3", "product_url": "https://kegland.com.au/products/x",
            "capacity_l": 19.0, "empty_distance_mm": 560, "full_distance_mm": 26}
    entry = MockConfigEntry(domain=DOMAIN, unique_id=ADDR, title="Tap 3", data={CONF_ADDRESS: ADDR}, options=opts)
    entry.add_to_hass(hass)
    lookup = AsyncMock(return_value=LookupResult("https://img/x.jpg", "Ginger"))
    with patch("homeassistant.components.bluetooth.async_register_callback", return_value=lambda: None), \
         patch("homeassistant.components.bluetooth.async_last_service_info", return_value=None), \
         patch("custom_components.rapt_laser_level.lookup.async_lookup_once", lookup):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert entry.data["drink_lookup"]["image"] == "https://img/x.jpg"
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    lookup.assert_awaited_once()  # second setup used the cache
    assert hass.states.get("sensor.tap_3_drink").attributes["entity_picture"] == "https://img/x.jpg"


async def test_remove_entry_allows_rediscovery(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=ADDR, title="Tap 3", data={CONF_ADDRESS: ADDR}, options={})
    entry.add_to_hass(hass)
    with patch("homeassistant.components.bluetooth.async_register_callback", return_value=lambda: None), \
         patch("homeassistant.components.bluetooth.async_last_service_info", return_value=None), \
         patch("homeassistant.components.bluetooth.async_rediscover_address") as rediscover:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    rediscover.assert_called_once_with(hass, ADDR)


KL = "https://kegland.com.au/products/fukusoda-yuzu-rtk"
KL_JS = {"title": "Hard FUKUSODA Yuzu", "featured_image": "//cdn.shopify.com/s/files/yuzu.jpg?v=1"}


def test_clean_product_url():
    from custom_components.rapt_laser_level.drink import clean_product_url, shopify_js_url
    assert clean_product_url(KL + "?pr_prod_strat=similar&pr_seq=uniform&variant=42") == KL + "?variant=42"
    assert clean_product_url(KL + "?utm_source=x#top") == KL
    assert shopify_js_url(KL + "?variant=42") == KL + ".js"
    assert shopify_js_url("https://example.com/beer/1") is None


async def test_lookup_shopify_first_single_request(hass: HomeAssistant, aioclient_mock):
    from custom_components.rapt_laser_level.drink import async_lookup
    import json
    aioclient_mock.get(KL + ".js", text=json.dumps(KL_JS))
    img, title = await async_lookup(hass, KL + "?pr_prod_strat=similar")
    assert img == "https://cdn.shopify.com/s/files/yuzu.jpg?v=1" and title == "Hard FUKUSODA Yuzu"
    assert aioclient_mock.call_count == 1


async def test_lookup_stops_after_429(hass: HomeAssistant, aioclient_mock):
    from custom_components.rapt_laser_level.drink import async_lookup
    aioclient_mock.get(KL + ".js", status=429)
    aioclient_mock.get(KL, text=PAGE)
    assert await async_lookup(hass, KL) == (None, None)
    assert aioclient_mock.call_count == 1  # never tried the page
    from custom_components.rapt_laser_level.drink import async_lookup_once
    res = await async_lookup_once(hass, KL)
    assert res.retry and res.image is None


async def test_lookup_404_not_retried(hass: HomeAssistant, aioclient_mock):
    from custom_components.rapt_laser_level.drink import async_lookup_once
    aioclient_mock.get(KL + ".js", status=404)
    aioclient_mock.get(KL, status=404)
    res = await async_lookup_once(hass, KL)
    assert not res.retry and res.image is None


def _patches(lookup):
    return (
        patch("homeassistant.components.bluetooth.async_register_callback", return_value=lambda: None),
        patch("homeassistant.components.bluetooth.async_last_service_info", return_value=None),
        patch("custom_components.rapt_laser_level.lookup.async_lookup_once", lookup),
    )


async def _advance(hass, seconds):
    from datetime import timedelta
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done(wait_background_tasks=True)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_schedule_survives_restart(hass: HomeAssistant):
    opts = {"keg_name": "Tap 3", "product_url": KL}
    entry = MockConfigEntry(domain=DOMAIN, unique_id=ADDR, title="Tap 3", data={CONF_ADDRESS: ADDR}, options=opts)
    entry.add_to_hass(hass)
    lookup = AsyncMock(return_value=LookupResult(retry=True, error="429 Too Many Requests"))
    p1, p2, p3 = _patches(lookup)
    with p1, p2, p3:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert lookup.await_count == 1
        # A reload (like a restart) waits for the saved retry time.
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert lookup.await_count == 1


async def test_lookup_non_shopify_reads_page(hass: HomeAssistant, aioclient_mock):
    from custom_components.rapt_laser_level.drink import async_lookup
    aioclient_mock.get("https://example.com/beer/1", text=PAGE)
    img, _ = await async_lookup(hass, "https://example.com/beer/1")
    assert img.startswith("https://kegland.com.au/cdn/")
    assert aioclient_mock.call_count == 1


def test_tidy_title():
    from custom_components.rapt_laser_level.drink import tidy_title
    assert tidy_title("Black Angel's Tennessee Bourbon and Cola Ready to Keg Kit") == "Black Angel's Tennessee Bourbon and Cola"
    assert tidy_title("Hard FUKUSODA Yuzu - Zero Sugar - Ready to Keg Kit") == "Hard FUKUSODA Yuzu - Zero Sugar"
    assert tidy_title("Diet Orange Stripe Brewed Ginger Beer - Jamaican - Ready To Keg Kit") == "Diet Orange Stripe Brewed Ginger Beer - Jamaican"
    assert tidy_title("Orange Stripe Ginger Beer - RTK") == "Orange Stripe Ginger Beer"
    assert tidy_title("Hartwork Pale Ale") == "Hartwork Pale Ale"
    assert tidy_title("Ready to Keg Kit") == "Ready to Keg Kit"  # never empty
    assert tidy_title(None) is None


def test_normalise_pasted_shopify_address():
    from custom_components.rapt_laser_level.config_flow import _normalise_url
    raw = '"\\/\\/cdn.shopify.com\\/s\\/files\\/1\\/x.jpg?v=1"'
    assert _normalise_url(raw) == "https://cdn.shopify.com/s/files/1/x.jpg?v=1"
    assert _normalise_url("//cdn.shopify.com/a.jpg") == "https://cdn.shopify.com/a.jpg"


async def test_one_attempt_then_no_retries(hass: HomeAssistant, freezer):
    opts = {"keg_name": "Tap 3", "product_url": KL}
    entry = MockConfigEntry(domain=DOMAIN, unique_id=ADDR, title="Tap 3", data={CONF_ADDRESS: ADDR}, options=opts)
    entry.add_to_hass(hass)
    lookup = AsyncMock(return_value=LookupResult(retry=True, error="429 Too Many Requests"))
    p1, p2, p3 = _patches(lookup)
    with p1, p2, p3:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        attrs = hass.states.get("sensor.tap_3_drink").attributes
        assert attrs["lookup_last_result"] == "429 Too Many Requests"
        freezer.tick(60 * 86400)
        await _advance(hass, 0)
        assert lookup.await_count == 1  # never asks again for the same link
        # A new link gets one attempt of its own.
        hass.config_entries.async_update_entry(entry, options={**opts, "product_url": KL + "-2"})
        await hass.async_block_till_done(wait_background_tasks=True)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert lookup.await_count == 2


async def test_card_saves_picture_found_by_browser(hass: HomeAssistant, hass_ws_client):
    from homeassistant.setup import async_setup_component
    opts = {"keg_name": "Tap 3", "product_url": KL}
    entry = MockConfigEntry(domain=DOMAIN, unique_id=ADDR, title="Tap 3", data={CONF_ADDRESS: ADDR}, options=opts)
    entry.add_to_hass(hass)
    lookup = AsyncMock(return_value=LookupResult(retry=True, error="429 Too Many Requests"))
    p1, p2, p3 = _patches(lookup)
    with p1, p2, p3:
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done(wait_background_tasks=True)
        client = await hass_ws_client(hass)
        await client.send_json({"id": 1, "type": "rapt_laser_level/drink_found",
            "entity_id": "sensor.tap_3_drink", "product_url": KL + "?utm_source=x",
            "image": "//cdn.shopify.com/s/files/x.jpg",
            "title": "Diet Orange Stripe Brewed Ginger Beer - Jamaican - Ready To Keg Kit"})
        msg = await client.receive_json()
        assert msg["success"], msg
        await hass.async_block_till_done(wait_background_tasks=True)
        await client.send_json({"id": 2, "type": "rapt_laser_level/drink_found",
            "entity_id": "sensor.tap_3_drink", "product_url": "https://evil.example/products/x",
            "image": "https://evil.example/x.jpg"})
        assert not (await client.receive_json())["success"]
    assert lookup.await_count == 1
    d = hass.states.get("sensor.tap_3_drink")
    assert d.attributes["entity_picture"] == "https://cdn.shopify.com/s/files/x.jpg"
    assert d.state == "Diet Orange Stripe Brewed Ginger Beer - Jamaican"
    assert d.attributes["lookup_last_result"] == "found by the dashboard card"
    assert hass.states.get("image.tap_3_drink_image") is not None

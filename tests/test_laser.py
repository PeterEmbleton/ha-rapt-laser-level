from unittest.mock import patch, MagicMock
from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_ADDRESS
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.components.bluetooth import BluetoothChange
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rapt_laser_level.parser import decode
from custom_components.rapt_laser_level.const import DOMAIN

ADDR = "CC:4E:21:36:BA:31"

def make_payload(dist=300, rem=260, batt=1000, temp=412, status=1):
    p = b"PT" + bytes([0xA1]) + bytes.fromhex("CC4E2136BA31")
    p += dist.to_bytes(2, "little") + rem.to_bytes(2, "little") + bytes([0x0A])
    p += batt.to_bytes(2, "little") + temp.to_bytes(2, "little", signed=True) + bytes([status])
    return p

def test_decode():
    r = decode(make_payload())
    assert r.distance_mm == 300 and r.remaining_mm == 260
    assert r.battery_pct == 100.0 and r.temperature_c == 4.12 and r.status == 1
    assert r.embedded_mac == ADDR
    assert decode(bytes.fromhex("5054514301")) is None  # Pete's real identity frame
    # Wolf-oz far sample: byte9=163, byte10=1 -> 419 mm as u16
    p = bytearray(make_payload()); p[9]=163; p[10]=1
    assert decode(bytes(p)).distance_mm == 419

def info(payload, rssi=-72):
    m = MagicMock()
    m.address = ADDR; m.rssi = rssi; m.name = "RAPT LaserLevelSensor"
    m.manufacturer_data = {16722: payload}
    return m

async def test_entry(hass: HomeAssistant):
    captured = {}
    def fake_register(hass_, cb, matcher, mode):
        captured["cb"] = cb
        return lambda: None
    entry = MockConfigEntry(domain=DOMAIN, unique_id=ADDR, title="Tap 4 - Beer",
        data={CONF_ADDRESS: ADDR},
        options={"keg_name": "Tap 4 - Beer", "capacity_l": 19.0, "empty_distance_mm": 560, "full_distance_mm": 26})
    entry.add_to_hass(hass)
    with patch("homeassistant.components.bluetooth.async_register_callback", fake_register), \
         patch("homeassistant.components.bluetooth.async_last_service_info", return_value=None):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    st = hass.states.get("sensor.tap_4_beer_level")
    assert st is not None and st.state == "unavailable", st
    captured["cb"](info(bytes.fromhex("5054514301")), BluetoothChange.ADVERTISEMENT)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.tap_4_beer_level").state == "unavailable"
    captured["cb"](info(make_payload(dist=293)), BluetoothChange.ADVERTISEMENT)
    await hass.async_block_till_done()
    lvl = hass.states.get("sensor.tap_4_beer_level").state
    vol = hass.states.get("sensor.tap_4_beer_volume").state
    print({s.entity_id: s.state for s in hass.states.async_all("sensor")})
    assert lvl == "50.0" and vol == "9.5"
    assert hass.states.get("sensor.tap_4_beer_temperature").state == "4.12"
    assert hass.states.get("sensor.tap_4_beer_status_code").attributes["packet_type"] == "0xA1"
    assert hass.states.get("sensor.tap_4_beer_activity").state == "Idle"
    captured["cb"](info(make_payload(dist=250, status=3)), BluetoothChange.ADVERTISEMENT)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.tap_4_beer_activity").state == "Rapid readings (pour)"
    captured["cb"](info(make_payload(dist=250, status=9)), BluetoothChange.ADVERTISEMENT)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.tap_4_beer_activity").state == "Unknown"

async def test_user_flow_manual(hass: HomeAssistant):
    with patch("custom_components.rapt_laser_level.config_flow.async_discovered_service_info", return_value=[]), \
         patch("custom_components.rapt_laser_level.async_setup_entry", return_value=True):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        assert r["type"] == FlowResultType.FORM and r["step_id"] == "user"
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_ADDRESS: ADDR.lower()})
        assert r["step_id"] == "keg"
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"keg_name": "Tap 4 - Beer", "capacity_l": 19, "empty_distance_mm": 560, "full_distance_mm": 26})
        assert r["type"] == FlowResultType.CREATE_ENTRY and r["result"].unique_id == ADDR

async def test_unchanged_adverts_keep_fresh(hass: HomeAssistant):
    from datetime import timedelta
    from bluetooth_data_tools import monotonic_time_coarse
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed
    captured = {}
    last = {"info": None}
    def fake_register(hass_, cb, matcher, mode):
        captured["cb"] = cb
        return lambda: None
    entry = MockConfigEntry(domain=DOMAIN, unique_id=ADDR, title="Tap 4 - Beer", data={CONF_ADDRESS: ADDR}, options={})
    entry.add_to_hass(hass)
    with patch("homeassistant.components.bluetooth.async_register_callback", fake_register), \
         patch("homeassistant.components.bluetooth.async_last_service_info", side_effect=lambda *a, **k: last["info"]):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        i = info(make_payload()); i.time = monotonic_time_coarse()
        captured["cb"](i, BluetoothChange.ADVERTISEMENT)
        await hass.async_block_till_done()
        first = hass.states.get("sensor.tap_4_beer_last_reading").state
        # 40 min of identical adverts: HA sends no callbacks, but history time moves on.
        future = dt_util.utcnow() + timedelta(minutes=140)
        i2 = info(make_payload()); i2.time = monotonic_time_coarse() + 8400
        last["info"] = i2
        with patch("custom_components.rapt_laser_level.monotonic_time_coarse", return_value=i2.time + 5), \
             patch("custom_components.rapt_laser_level.dt_util.utcnow", return_value=future):
            async_fire_time_changed(hass, future)
            await hass.async_block_till_done()
        assert hass.states.get("sensor.tap_4_beer_level").state != "unavailable"
        assert hass.states.get("sensor.tap_4_beer_last_reading").state != first
        # Then silence: no new history -> stale after 30 more minutes.
        future2 = future + timedelta(minutes=151)
        with patch("custom_components.rapt_laser_level.monotonic_time_coarse", return_value=i2.time + 5 + 9060), \
             patch("custom_components.rapt_laser_level.dt_util.utcnow", return_value=future2):
            async_fire_time_changed(hass, future2)
            await hass.async_block_till_done()
        assert hass.states.get("sensor.tap_4_beer_level").state == "unavailable"


def test_power_on_placeholder_ignored():
    # Real packet from Tap 3 just after it was switched on.
    assert decode(bytes.fromhex("5054a1ee90a80df5380000000000ffff008001")) is None

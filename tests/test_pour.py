from datetime import datetime, timezone
from unittest.mock import patch

from homeassistant.components.bluetooth import BluetoothChange
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.rapt_laser_level.const import DOMAIN, EVENT_POUR
from custom_components.rapt_laser_level.parser import decode
from custom_components.rapt_laser_level.pour import PourTracker

from .test_laser import ADDR, info, make_payload

NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)
MM_TO_L = 19.0 / (560 - 26)


def r(dist, status):
    return decode(make_payload(dist=dist, status=status))


def feed(t, readings):
    out = [t.reading(r(d, s), NOW, MM_TO_L, 0.2) for d, s in readings]
    return [p for p in out if p]


def test_surface_wobble_is_not_a_pour():
    # Tap 1 on 2026-11-20 after a new proxy: calm 271 mm, then 90 minutes of
    # rapid readings between 263 and 276 mm with nobody pouring.
    t = PourTracker()
    wobble = [263, 276, 266, 274, 265, 272, 269, 275, 264, 273, 271, 274, 273]
    assert feed(t, [(271, 2)] + [(d, 3) for d in wobble]) == []
    assert t.quiet(NOW, MM_TO_L, 0.2) is None
    assert t.calm_mm == 273  # median of the last five


def test_real_pour_measured_from_calm_to_calm():
    t = PourTracker()
    pours = feed(t, [(271, 2), (268, 3), (279, 3), (290, 3), (283, 3), (284, 2)])
    assert len(pours) == 1 and pours[0].litres == round(13 * MM_TO_L, 2)  # ~0.46 L


def test_pour_settles_on_median_when_sensor_goes_quiet():
    t = PourTracker()
    assert feed(t, [(271, 2), (275, 3), (282, 3), (286, 3), (283, 3), (285, 3), (284, 3)]) == []
    pour = t.quiet(NOW, MM_TO_L, 0.2)
    assert pour is not None and pour.after_mm == 284


def test_missed_burst_still_counts():
    t = PourTracker()
    assert len(feed(t, [(271, 2), (271, 1), (286, 1)])) == 1


async def test_pour_event_and_sensor(hass: HomeAssistant):
    captured = {}

    def fake_register(hass_, cb, matcher, mode):
        captured["cb"] = cb
        return lambda: None

    entry = MockConfigEntry(domain=DOMAIN, unique_id=ADDR, title="Tap 4 - Beer",
                            data={CONF_ADDRESS: ADDR}, options={"drink_type": "Pale Ale"})
    entry.add_to_hass(hass)
    events = async_capture_events(hass, EVENT_POUR)
    with patch("homeassistant.components.bluetooth.async_register_callback", fake_register), \
         patch("homeassistant.components.bluetooth.async_last_service_info", return_value=None):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        for d, s in [(271, 2), (268, 3), (290, 3), (284, 2)]:
            captured["cb"](info(make_payload(dist=d, status=s)), BluetoothChange.ADVERTISEMENT)
        await hass.async_block_till_done()
    assert len(events) == 1
    data = events[0].data
    assert data["tap"] == "Tap 4 - Beer" and data["drink"] == "Pale Ale" and data["device_id"]
    assert data["litres"] == round(13 * MM_TO_L, 2)
    st = hass.states.get("sensor.tap_4_beer_last_pour")
    assert float(st.state) == data["litres"] and "poured_at" in st.attributes

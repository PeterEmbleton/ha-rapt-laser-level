# RAPT Laser Level (local BLE) for Home Assistant

**Unofficial community integration.** It isn't affiliated with, endorsed or supported by RAPT or KegLand. The RAPT name and logo belong to their owners and are used only to identify compatible hardware.

This integration reads [RAPT Laser Level Sensors](https://docs.rapt.io/equipment/rapt-laser-level-sensor/assembly) (the KegLand keg lid sensor) straight from their Bluetooth broadcasts. It needs no RAPT cloud, no other RAPT gateway, and no pairing. Home Assistant only listens and never sends anything to the sensor.

## What you get

Each sensor becomes a device with these entities:

| Entity | What it shows |
| --- | --- |
| Level | Percent full, worked out from your keg's calibration |
| Volume | Litres remaining |
| Distance | Raw distance from the sensor to the liquid (mm) |
| Temperature | Sensor temperature (°C) |
| Battery | Battery % (diagnostic) |
| Activity | `Idle`, `Single reading` or `Rapid readings (pour)` |
| Status code / Remaining height / Last reading / Signal strength | Diagnostics (signal strength is disabled by default) |
| Drink | The drink's name, with its picture as the entity picture, and the picture lookup's status as attributes |
| Drink image | The drink's picture, for picture cards (only created when a picture is set) |

## Requirements

- Home Assistant 2026.3 or later.
- A Bluetooth adapter that can hear the sensors: the HA host's own adapter or an [ESPHome Bluetooth proxy](https://esphome.io/components/bluetooth_proxy.html) near the kegs. Kegerators and keezers are metal boxes, so a proxy close by helps.

## Install

### Marketplace (HACS), as a custom repository

1. Open the Marketplace (HACS) → ⋮ → **Custom repositories**.
2. Add `https://github.com/PeterEmbleton/ha-rapt-laser-level`, type **Integration**.
3. Find **RAPT Laser Level (local BLE)**, install it, and restart Home Assistant.

### Manual

Copy `custom_components/rapt_laser_level` into your `/config/custom_components/` folder and restart Home Assistant.

## Set up

Sensors are usually discovered automatically. Otherwise go to **Settings → Devices & services → Add integration → RAPT Laser Level (local BLE)** and either pick the sensor or type its Bluetooth address.

HA's built-in **RAPT Bluetooth** integration (for the RAPT Pill) may also offer the same sensors, because they use the same Bluetooth manufacturer ID. Click **Ignore** on those, since that integration can't decode laser sensor packets.

Press the sensor's Bluetooth button once to make it send a reading straight away.

### Keg settings (Configure)

| Option | Default | Notes |
| --- | --- | --- |
| Name | | e.g. `Tap 3 - Ginger Beer` |
| Drink | | Leave blank to use the product page's title |
| Product page URL | | Optional. Tracking parameters are removed. For Shopify stores like KegLand the picture comes from the product feed (`/products/<name>.js`, one request, picture hosted on cdn.shopify.com); for other sites it comes from the page's `og:image` tag. See [Drink picture lookups](#drink-picture-lookups) for how often the shop is asked |
| Image URL | | Optional. Overrides the product page picture |
| Capacity (L) | 19 | |
| Empty distance (mm) | 560 | The sensor's reading with the keg empty. KegLand's default is 560 mm for 19 L and 290 mm for 9.5 L |
| Full distance (mm) | 26 | The reading when full, under pressure |

Level % = (empty − distance) ÷ (empty − full), clamped to 0–100. Volume = capacity × level. This assumes a straight-sided keg.

Saving the options reloads that keg; no restart needed.

## Drink picture lookups

The picture and name come from the keg's **Product page URL**, unless you fill in **Image URL** and **Drink** yourself. There is no polling:

- **Home Assistant asks once** when the product URL is set or changed: one request (two for non-Shopify sites), no retries, no periodic checks. The attempt is remembered, so restarts don't repeat it.
- **KegLand refuses Home Assistant's own requests** ("429 Too Many Requests"), even though it answers browsers. So for Shopify shops like KegLand, the **RAPT Keg card** looks a missing picture up from your browser when an admin views the dashboard, as a normal visitor would, and hands it to Home Assistant to save. It tries each product at most once every 6 hours, and stops once the picture is saved. Turn this off with `find_pictures: false`.
- The keg's **Drink** sensor shows `lookup_last_attempt` and `lookup_last_result` (`found`, `found by the dashboard card`, or why it failed).
- Still nothing? Open the product page, copy the image address into **Image URL**, and set **Drink** yourself.

## How often it updates

The sensor sends routine telemetry once an hour, and extra readings whenever the level changes. While it is pouring it sends a reading about once a second (status 3, LED flashing green). When a pour finishes the sensor just stops sending, so **Activity** goes back to `Idle` after 30 seconds with no new reading (the raw **Status code** keeps the last value the sensor sent). Home Assistant drops Bluetooth packets that are identical to the previous one. To cope with that, the integration refreshes **Last reading** from the last time HA heard the sensor, and only marks the sensor unavailable after **2.5 hours** without a reading.

## Dashboard card

The integration comes with a **RAPT Keg card**. It loads on every dashboard by itself, so there's nothing else to install. Edit a dashboard, choose **Add card** and pick **RAPT Keg card**, or use it in YAML:

```yaml
type: custom:rapt-keg-card
title: On tap          # optional, blank for no title
sort: device           # device (default), drink, or level (lowest first)
warning: 15            # bar turns yellow below this %
critical: 10           # bar turns red at or below this %
show_image: true
show_volume: true
show_temperature: true
find_pictures: true   # look missing drink pictures up from your browser
```

It finds every keg by itself and shows its drink picture, name, tap, a level bar, litres and temperature, plus "pouring now" during a pour. Tapping a keg opens its level history. After updating the integration, refresh the browser (or clear the app's frontend cache) to get the new card.

If you'd rather not use a custom card, [`examples/on-tap-card.yaml`](examples/on-tap-card.yaml) is a plain Markdown card that does much the same.

## Bluetooth packet format

Manufacturer ID `0x4152` ("RA"). Offsets below are into the manufacturer data that follows the ID:

| Bytes | Field |
| --- | --- |
| 0–1 | `PT` |
| 2 | Packet type (`0xA1` for readings) |
| 3–8 | The sensor's MAC |
| 9–10 | Distance, uint16 LE, mm |
| 11–12 | Remaining height, uint16 LE, mm (the sensor's own empty setting minus distance) |
| 13 | Version |
| 14–15 | Battery, uint16 LE ÷ 10, % |
| 16–17 | Temperature, int16 LE ÷ 100, °C |
| 18 | Status: 1 idle, 2 single reading, 3 rapid readings (pour). These meanings come from observation and aren't documented by RAPT |

Short `PTQC…` frames are identity packets and are ignored.

Thanks to the community projects that worked this out: [YDraig/rapt-laser-level-display](https://github.com/YDraig/rapt-laser-level-display), [BestEnemies/Rapt-Details](https://github.com/BestEnemies/Rapt-Details) and [thewolf-oz/rapt-laser-level](https://github.com/thewolf-oz/rapt-laser-level).

## Development

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
python -m pytest -q tests
```

CI runs the tests, Home Assistant's `hassfest` validator and the HACS validator on every push and pull request.

## Licence

MIT. See [LICENSE](LICENSE). The RAPT logo in `brand/` belongs to its owner and isn't covered by this licence.

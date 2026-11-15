"""Constants for the RAPT Laser Level (local BLE) integration."""

DOMAIN = "rapt_laser_level"
MANUFACTURER = "RAPT / KegLand"
MODEL = "Laser Level Sensor"

# Manufacturer id 0x4152 ("RA" little-endian); payload starts with "PT".
RAPT_MANUFACTURER_ID = 0x4152

CONF_KEG_NAME = "keg_name"
CONF_CAPACITY_L = "capacity_l"
CONF_EMPTY_MM = "empty_distance_mm"
CONF_FULL_MM = "full_distance_mm"
CONF_DRINK = "drink_type"
CONF_PRODUCT_URL = "product_url"
CONF_IMAGE_URL = "image_url"

# entry.data key holding the last successful product-page lookup.
CACHE_KEY = "drink_lookup"

# KegLand defaults for a 19 L ball-lock keg (see YDraig docs).
DEFAULT_CAPACITY_L = 19.0
DEFAULT_EMPTY_MM = 560
DEFAULT_FULL_MM = 26

# The sensor sends routine telemetry hourly (RAPT app: Telemetry Frequency 1 h),
# plus extra readings when the level changes. Allow two missed hourly reports.
STALE_AFTER_S = 150 * 60

# Background drink-picture lookup: wait this long (seconds) before each retry
# after a temporary failure such as KegLand's 429. Seven attempts in all.
LOOKUP_RETRY_DELAYS = (15 * 60, 60 * 60, 3 * 3600, 6 * 3600, 12 * 3600, 24 * 3600)

# While pouring the sensor sends about one reading a second (status 3). This
# long with no new reading means the pour has finished.
POUR_ENDED_AFTER_S = 30

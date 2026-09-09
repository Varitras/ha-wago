"""Constants of the WAGO 879 integration."""

from homeassistant.const import CONF_HOST, CONF_PORT

DOMAIN = "wago_879"

CONF_UNIT_ID = "unit_id"
CONF_MEASUREMENT_INTERVAL = "measurement_interval"
CONF_ENERGY_INTERVAL = "energy_interval"

DEFAULT_PORT = 502
DEFAULT_UNIT_ID = 1
# What the replaced YAML block polled with: core modbus' 15 s default for the
# instantaneous values, 300 s set by hand for the counters.
DEFAULT_MEASUREMENT_INTERVAL = 15
DEFAULT_ENERGY_INTERVAL = 300
INTERVAL_MIN_SECONDS = 5
INTERVAL_MAX_SECONDS = 3600

__all__ = [
    "CONF_ENERGY_INTERVAL",
    "CONF_HOST",
    "CONF_MEASUREMENT_INTERVAL",
    "CONF_PORT",
    "CONF_UNIT_ID",
    "DEFAULT_ENERGY_INTERVAL",
    "DEFAULT_MEASUREMENT_INTERVAL",
    "DEFAULT_PORT",
    "DEFAULT_UNIT_ID",
    "DOMAIN",
    "INTERVAL_MAX_SECONDS",
    "INTERVAL_MIN_SECONDS",
]

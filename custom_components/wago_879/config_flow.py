"""Config, reconfigure and options flows."""

from __future__ import annotations

from typing import Any

from modbus_connection import ModbusError, ModbusTcpParams
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.modbus import async_get_temporary_unit
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
)

from .const import (
    CONF_ENERGY_INTERVAL,
    CONF_HOST,
    CONF_MEASUREMENT_INTERVAL,
    CONF_PORT,
    CONF_UNIT_ID,
    CONNECTION_FIELDS,
    DEFAULT_ENERGY_INTERVAL,
    DEFAULT_MEASUREMENT_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_UNIT_ID,
    DOMAIN,
    INTERVAL_FIELDS,
    INTERVAL_MAX_SECONDS,
    INTERVAL_MIN_SECONDS,
    INTERVALS_IN_OPTIONS_MINOR_VERSION,
)
from .sensor import device_name
from .wago_879_api.device import WagoMeter

PORT_MAX = 65535
# The highest address a Modbus unit can have; 0 is broadcast.
UNIT_ID_MAX = 247
SECONDS = "s"
# Named tuple, not an inline literal in the `except` clause: at this project's
# `target-version = "py314"` the formatter drops the parentheses (PEP 758
# allows that from 3.14 on), and the file then no longer parses under the
# older interpreters that still read this tree - the Windows-side tooling
# among them.
_PROBE_FAILURES = (ModbusError, TimeoutError)


def _whole_number(minimum: int, maximum: int) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum, max=maximum, step=1, mode=NumberSelectorMode.BOX
        )
    )


def _seconds(minimum: int, maximum: int) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=1,
            mode=NumberSelectorMode.BOX,
            unit_of_measurement=SECONDS,
        )
    )


def _as_integers(user_input: dict[str, Any]) -> dict[str, Any]:
    """The form's numbers as integers: a number selector hands over floats."""
    return {
        key: value if key == CONF_HOST else int(value)
        for key, value in user_input.items()
    }


def _connection_schema(defaults: dict[str, Any]) -> dict[Any, Any]:
    return {
        vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): TextSelector(),
        vol.Required(
            CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)
        ): _whole_number(1, PORT_MAX),
        vol.Required(
            CONF_UNIT_ID, default=defaults.get(CONF_UNIT_ID, DEFAULT_UNIT_ID)
        ): _whole_number(1, UNIT_ID_MAX),
    }


def _interval_schema(defaults: dict[str, Any]) -> dict[Any, Any]:
    return {
        vol.Required(
            CONF_MEASUREMENT_INTERVAL,
            default=defaults.get(
                CONF_MEASUREMENT_INTERVAL, DEFAULT_MEASUREMENT_INTERVAL
            ),
        ): _seconds(INTERVAL_MIN_SECONDS, INTERVAL_MAX_SECONDS),
        vol.Required(
            CONF_ENERGY_INTERVAL,
            default=defaults.get(CONF_ENERGY_INTERVAL, DEFAULT_ENERGY_INTERVAL),
        ): _seconds(INTERVAL_MIN_SECONDS, INTERVAL_MAX_SECONDS),
    }


def normalised_host(user_input: dict[str, Any]) -> str | None:
    """The host without surrounding blanks; None when nothing usable was typed."""
    host = str(user_input.get(CONF_HOST, "")).strip()
    if not host or any(character.isspace() for character in host):
        return None
    return host


async def probe_serial(
    hass: HomeAssistant, user_input: dict[str, Any]
) -> tuple[str | None, str | None]:
    """The meter's serial at the given address, or the form error key instead."""
    params = ModbusTcpParams(
        host=user_input[CONF_HOST], port=int(user_input[CONF_PORT])
    )
    try:
        async with async_get_temporary_unit(
            hass, params, int(user_input[CONF_UNIT_ID])
        ) as unit:
            meter = WagoMeter(unit)
            await meter.async_read_identity()
    except _PROBE_FAILURES:
        return None, "cannot_connect"
    except HomeAssistantError:
        # The only HomeAssistantError the helper raises: another consumer holds
        # this endpoint with link settings that cannot both be honoured. The
        # address is fine, so this is not cannot_connect.
        return None, "link_settings_clash"
    return meter.serial_number, None


class WagoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Host, port, unit id and the two intervals."""

    VERSION = 1
    MINOR_VERSION = INTERVALS_IN_OPTIONS_MINOR_VERSION

    @staticmethod
    @callback
    def async_get_options_flow(entry: config_entries.ConfigEntry) -> WagoOptionsFlow:
        """The options flow for the intervals."""
        return WagoOptionsFlow()

    async def _validate(
        self, user_input: dict[str, Any]
    ) -> tuple[dict[str, str], str | None]:
        host = normalised_host(user_input)
        if host is None:
            return {"base": "invalid_host"}, None
        user_input.update(_as_integers(user_input))
        user_input[CONF_HOST] = host
        serial, error = await probe_serial(self.hass, user_input)
        if error is not None:
            return {"base": error}, None
        return {}, serial

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Create the entry once the meter answered with its serial."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, serial = await self._validate(user_input)
            if not errors:
                assert serial is not None
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=device_name(serial),
                    data={key: user_input[key] for key in CONNECTION_FIELDS},
                    options={key: user_input[key] for key in INTERVAL_FIELDS},
                )
        schema = vol.Schema(
            {
                **_connection_schema(user_input or {}),
                **_interval_schema(user_input or {}),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Move the entry to another address of the same meter."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, serial = await self._validate(user_input)
            if not errors:
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_mismatch()
                # Read before the update: the listener runs eagerly inside
                # async_update_entry and its reload unloads the entry, which
                # takes the listener off again.
                reloads_itself = bool(entry.update_listeners)
                # Not async_update_reload_and_abort: it schedules a reload of
                # its own on top of the one the entry's update listener already
                # performs, so the Modbus unit would be torn down and rebuilt
                # twice. Core reports that combination and drops it in 2026.12.
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, **user_input}
                )
                if not reloads_itself:
                    # Only a successful setup registers the listener, so an
                    # entry in SETUP_ERROR or NOT_LOADED - the very entry a
                    # user reconfigures to repair - would otherwise stay down
                    # while the flow reports success.
                    self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_abort(reason="reconfigure_successful")
        schema = vol.Schema(_connection_schema(user_input or dict(entry.data)))
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )


class WagoOptionsFlow(config_entries.OptionsFlow):
    """The two poll intervals; the entry reloads on change."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """The one options page."""
        if user_input is not None:
            return self.async_create_entry(data=_as_integers(user_input))
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(_interval_schema(current))
        )

"""Config, reconfigure and options flows."""

from __future__ import annotations

from collections.abc import Sequence
from ipaddress import ip_address
from typing import Any

from modbus_connection import ModbusError, ModbusTcpParams
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.modbus import async_get_temporary_unit
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
from homeassistant.helpers.typing import DiscoveryInfoType

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
from .discovery import async_find_modules
from .sensor import device_name, entry_title
from .wago_879_api.device import UnsupportedMeter, WagoMeter
from .wago_879_api.discovery import FoundModule

PORT_MAX = 65535
# The highest address a Modbus unit can have; 0 is broadcast.
UNIT_ID_MAX = 247
SECONDS = "s"
NOT_A_WHOLE_NUMBER = "not_a_whole_number"
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


def _as_integers(user_input: dict[str, Any]) -> dict[str, Any] | None:
    """The form's numbers as integers, or None when one is not a whole number.

    A number selector hands over floats, and its step of 1 binds the UI only:
    the flow API takes 502.5 or "nan" as well. Rounding would store a setting
    nobody entered, so anything not whole and finite is refused.
    """
    converted: dict[str, Any] = {}
    for key, value in user_input.items():
        if key == CONF_HOST:
            converted[key] = value
            continue
        try:
            number = float(value)
        except TypeError, ValueError:
            return None
        if not number.is_integer():
            return None
        converted[key] = int(number)
    return converted


def _host_selector(found: Sequence[FoundModule]) -> TextSelector | SelectSelector:
    """A text field, or a list of the modules found that takes typed text too.

    A module in another subnet never answers the search.
    """
    if not found:
        return TextSelector()
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(
                    value=module.host, label=f"{module.host} ({module.serial_number})"
                )
                for module in found
            ],
            custom_value=True,
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


def _connection_schema(
    defaults: dict[str, Any], found: Sequence[FoundModule] = ()
) -> dict[Any, Any]:
    first_found = found[0].host if found else ""
    return {
        vol.Required(
            CONF_HOST, default=defaults.get(CONF_HOST, first_found)
        ): _host_selector(found),
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
    except UnsupportedMeter:
        return None, "unsupported_meter"
    except HomeAssistantError:
        # The only HomeAssistantError the helper raises: another consumer holds
        # this endpoint with link settings that cannot both be honoured. The
        # address is fine, so this is not cannot_connect.
        return None, "link_settings_clash"
    return meter.serial_number, None


def _is_address(host: str) -> bool:
    try:
        ip_address(host)
    except ValueError:
        return False
    return True


def _entry_of(
    hass: HomeAssistant,
    entries: Sequence[config_entries.ConfigEntry],
    module: FoundModule,
) -> config_entries.ConfigEntry | None:
    """The entry that already reaches `module`, or None.

    Matched by its address, or by the module device its setup registered.
    """
    devices = dr.async_get(hass)
    identifier = (DOMAIN, module.serial_number)
    for entry in entries:
        if entry.data.get(CONF_HOST) == module.host:
            return entry
        if devices.async_get_device_by_identifier(identifier, entry.entry_id):
            return entry
    return None


@callback
def async_move_entry(
    hass: HomeAssistant, entry: config_entries.ConfigEntry, data: dict[str, Any]
) -> None:
    """Give `entry` new connection data and reload it exactly once."""
    # Read before the update: the listener runs eagerly inside
    # async_update_entry and its reload unloads the entry, which takes the
    # listener off again.
    reloads_itself = bool(entry.update_listeners)
    # Not async_update_reload_and_abort: it schedules a reload of its own on
    # top of the one the entry's update listener already performs, so the
    # Modbus unit would be torn down and rebuilt twice. Core reports that
    # combination and drops it in 2026.12.
    # The old address is known only here: setup compares the title with the
    # saved host, which this update is about to replace.
    title = entry.title
    if entry.unique_id is not None:
        title = entry_title(entry.title, entry.data[CONF_HOST], entry.unique_id)
    hass.config_entries.async_update_entry(entry, data=data, title=title)
    if not reloads_itself:
        # Only a successful setup registers the listener, so an entry in
        # SETUP_ERROR or NOT_LOADED - the very entry a user moves to repair
        # it - would otherwise stay down while the flow reports success.
        hass.config_entries.async_schedule_reload(entry.entry_id)


class WagoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Host, port, unit id and the two intervals."""

    VERSION = 1
    MINOR_VERSION = INTERVALS_IN_OPTIONS_MINOR_VERSION

    def __init__(self) -> None:
        """Nothing searched for, nothing found yet."""
        self._found: list[FoundModule] | None = None
        self._discovered: dict[str, Any] = {}

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
        whole = _as_integers(user_input)
        if whole is None:
            return {"base": NOT_A_WHOLE_NUMBER}, None
        user_input.update(whole)
        user_input[CONF_HOST] = host
        serial, error = await probe_serial(self.hass, user_input)
        if error is not None:
            return {"base": error}, None
        return {}, serial

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Create the entry once the meter answered with its serial."""
        if self._found is None:
            # Once per flow: the search takes seconds, and a form shown again
            # after an error should not wait for it twice.
            entries = self._async_current_entries()
            self._found = [
                module
                for module in await async_find_modules(self.hass)
                if _entry_of(self.hass, entries, module) is None
            ]
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
                **_connection_schema(user_input or {}, self._found),
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
                assert serial is not None
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_mismatch()
                async_move_entry(self.hass, entry, {**entry.data, **user_input})
                return self.async_abort(reason="reconfigure_successful")
        schema = vol.Schema(_connection_schema(user_input or dict(entry.data)))
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )

    async def async_step_integration_discovery(
        self, discovery_info: DiscoveryInfoType
    ) -> config_entries.ConfigFlowResult:
        """A module the search found: its entry follows it, or it is offered."""
        module = FoundModule(**discovery_info)
        entry = _entry_of(
            self.hass, self._async_current_entries(include_ignore=False), module
        )
        if entry is not None:
            return self._follow(entry, module.host)
        connection = {
            CONF_HOST: module.host,
            CONF_PORT: DEFAULT_PORT,
            CONF_UNIT_ID: DEFAULT_UNIT_ID,
        }
        # ponytail: a meter on another unit id or port is not offered; adding
        # it by hand still works.
        serial, error = await probe_serial(self.hass, connection)
        if error is not None:
            return self.async_abort(reason=error)
        assert serial is not None
        await self.async_set_unique_id(serial)
        # An entry that never reached the module has no module device, but
        # the meter's serial still names it.
        entry = self.hass.config_entries.async_entry_for_domain_unique_id(
            DOMAIN, serial
        )
        if entry is not None and entry.source != config_entries.SOURCE_IGNORE:
            return self._follow(entry, module.host)
        self._abort_if_unique_id_configured()
        self._discovered = connection
        self.context["title_placeholders"] = {"name": device_name(serial)}
        return await self.async_step_discovery_confirm()

    def _follow(
        self, entry: config_entries.ConfigEntry, host: str
    ) -> config_entries.ConfigFlowResult:
        """Point a known entry at the address its module was found at."""
        # A host name follows the module through DHCP by itself.
        moved = entry.data[CONF_HOST] != host
        if moved and _is_address(entry.data[CONF_HOST]):
            async_move_entry(self.hass, entry, {**entry.data, CONF_HOST: host})
        return self.async_abort(reason="already_configured")

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Add a found meter with the default intervals."""
        assert self.unique_id is not None
        name = device_name(self.unique_id)
        if user_input is None:
            return self.async_show_form(
                step_id="discovery_confirm",
                description_placeholders={
                    "name": name,
                    "host": self._discovered[CONF_HOST],
                },
            )
        return self.async_create_entry(
            title=name,
            data=self._discovered,
            options={
                CONF_MEASUREMENT_INTERVAL: DEFAULT_MEASUREMENT_INTERVAL,
                CONF_ENERGY_INTERVAL: DEFAULT_ENERGY_INTERVAL,
            },
        )


class WagoOptionsFlow(config_entries.OptionsFlow):
    """The two poll intervals; the entry reloads on change."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """The one options page."""
        errors: dict[str, str] = {}
        if user_input is not None:
            whole = _as_integers(user_input)
            if whole is not None:
                return self.async_create_entry(data=whole)
            errors["base"] = NOT_A_WHOLE_NUMBER
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(_interval_schema(current)),
            errors=errors,
        )

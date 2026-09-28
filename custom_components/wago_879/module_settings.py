"""What the module settings form accepts, as the values the module reports."""

from __future__ import annotations

from ipaddress import AddressValueError, IPv4Address, IPv4Interface, NetmaskValueError
import math
import re
from typing import Any

from .wago_879_api.registers import HOSTNAME_WORDS

# How the module stores a server or gateway that is not set; its tool
# writes it for a field left empty.
UNSET = "0.0.0.0"
ADDRESS_FIELDS = ("ip_address", "gateway")
SERVER_FIELDS = ("dns_server_1", "dns_server_2", "ntp_server_1", "ntp_server_2")
SWITCH_FIELDS = ("dhcp", "ntp")
# One label of a DNS name (RFC 1123). One byte short of the register block:
# whether the firmware needs a terminating NUL is unknown, and a name that
# fills the block would lose it.
HOSTNAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", re.ASCII)
HOSTNAME_MAX_LENGTH = HOSTNAME_WORDS * 2 - 1
# A named tuple for the same reason as config_flow's _PROBE_FAILURES.
_NOT_A_NUMBER = (TypeError, ValueError)


class _Refused(Exception):
    """A form value the module cannot use."""

    def __init__(self, error: str) -> None:
        super().__init__(error)
        self.error = error


def _address(text: Any) -> str:
    """The address typed, or UNSET for nothing typed."""
    try:
        return str(IPv4Address(str(text or "").strip() or UNSET))
    except AddressValueError:
        raise _Refused("invalid_address") from None


def _hostname(text: Any) -> str:
    hostname = str(text or "")
    if len(hostname) > HOSTNAME_MAX_LENGTH or not HOSTNAME.fullmatch(hostname):
        raise _Refused("invalid_hostname")
    return hostname


def _whole_number(value: Any) -> int:
    """A number selector hands over floats; the flow API takes any text."""
    try:
        number = float(value)
    except _NOT_A_NUMBER:
        raise _Refused("not_a_whole_number") from None
    if not math.isfinite(number) or not number.is_integer():
        raise _Refused("not_a_whole_number")
    return int(number)


def _check_network(interface: IPv4Interface, gateway_text: str) -> None:
    """Refuse a fixed address that cannot work in its network."""
    network = interface.network
    host = interface.ip
    if host in (network.network_address, network.broadcast_address):
        raise _Refused("invalid_address")
    gateway = IPv4Address(gateway_text)
    if gateway_text != UNSET and (gateway not in network or gateway == host):
        raise _Refused("gateway_outside_network")


def _settings(form: dict[str, Any]) -> dict[str, Any]:
    settings: dict[str, Any] = {key: bool(form.get(key)) for key in SWITCH_FIELDS}
    for key in (*ADDRESS_FIELDS, "netmask", *SERVER_FIELDS):
        settings[key] = _address(form.get(key))
    settings["hostname"] = _hostname(form.get("hostname"))
    settings["timeout"] = _whole_number(form.get("timeout"))
    # Checked with DHCP on too: the fixed settings are written either way and
    # take effect the moment DHCP is switched off.
    try:
        interface = IPv4Interface(f"{settings['ip_address']}/{settings['netmask']}")
    except NetmaskValueError:
        raise _Refused("invalid_netmask") from None
    if settings["dhcp"]:
        return settings
    if settings["ip_address"] == UNSET:
        raise _Refused("invalid_address")
    _check_network(interface, settings["gateway"])
    return settings


def module_settings(form: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """The settings to write, or the form error that stops them.

    Keyed and typed as the module reports them, so a change can be told
    from none.
    """
    try:
        return _settings(form), None
    except _Refused as refused:
        return None, refused.error

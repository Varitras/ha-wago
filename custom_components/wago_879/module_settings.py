"""What the module settings form accepts, as the values the module reports."""

from __future__ import annotations

from ipaddress import AddressValueError, IPv4Address, IPv4Interface, NetmaskValueError
import math
import re
from typing import Any

from .wago_879_api.addresses import is_host_address
from .wago_879_api.device import MODULE_SERVERS, MODULE_SWITCHES, WORD_RANGE
from .wago_879_api.registers import HOSTNAME_WORDS

# How the module stores a server or gateway that is not set; its tool
# writes it for a field left empty.
UNSET = "0.0.0.0"
# Addresses the form shows empty when they are not set.
OPTIONAL_ADDRESSES = ("ip_address", "gateway", *MODULE_SERVERS)
# What decides where the module sits in its network.
NETWORK_FIELDS = frozenset({"dhcp", "ip_address", "netmask", "gateway"})
# A netmask a host can live in: /31 and /32 leave no address besides the
# network and broadcast addresses the fixed address may not take.
PREFIX_RANGE = range(1, 31)
# One label of a DNS name (RFC 1123). One byte short of the register block:
# whether the firmware needs a terminating NUL is unknown, and a name that
# fills the block would lose it.
HOSTNAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", re.ASCII)
HOSTNAME_MAX_LENGTH = HOSTNAME_WORDS * 2 - 1
# One register word, and a module that waits no time at all for the meter
# never gets an answer; the module's own limits are not known.
TIMEOUT_RANGE = range(1, WORD_RANGE)
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


def _whole_number(value: Any) -> int:
    """A number selector hands over floats; the flow API takes any text."""
    try:
        number = float(value)
    except _NOT_A_NUMBER:
        raise _Refused("not_a_whole_number") from None
    if not math.isfinite(number) or not number.is_integer():
        raise _Refused("not_a_whole_number")
    return int(number)


def _check_server(text: str) -> None:
    if text != UNSET and not is_host_address(IPv4Address(text)):
        raise _Refused("invalid_address")


def _check_hostname(hostname: str) -> None:
    if len(hostname) > HOSTNAME_MAX_LENGTH or not HOSTNAME.fullmatch(hostname):
        raise _Refused("invalid_hostname")


def _interface(settings: dict[str, Any]) -> IPv4Interface:
    """The fixed address in its network, with the netmask exactly as typed.

    `IPv4Interface` also reads a host mask - 0.0.0.255 as /24 - but the
    module would get the words as typed, so only the canonical form passes.
    """
    try:
        interface = IPv4Interface(f"{settings['ip_address']}/{settings['netmask']}")
    except NetmaskValueError:
        raise _Refused("invalid_netmask") from None
    canonical = str(interface.netmask) == settings["netmask"]
    if not canonical or interface.network.prefixlen not in PREFIX_RANGE:
        raise _Refused("invalid_netmask")
    return interface


def _check_network(settings: dict[str, Any]) -> None:
    """Refuse a fixed address or gateway that cannot work in its network."""
    interface = _interface(settings)
    network = interface.network
    edges = (network.network_address, network.broadcast_address)
    host = interface.ip
    if not is_host_address(host) or host in edges:
        raise _Refused("invalid_address")
    if settings["gateway"] == UNSET:
        return
    gateway = IPv4Address(settings["gateway"])
    usable = is_host_address(gateway) and gateway not in edges
    if not usable or gateway not in network or gateway == host:
        raise _Refused("gateway_outside_network")


def _settings(form: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    settings: dict[str, Any] = {key: bool(form.get(key)) for key in MODULE_SWITCHES}
    for key in (*OPTIONAL_ADDRESSES, "netmask"):
        settings[key] = _address(form.get(key))
    settings["hostname"] = str(form.get("hostname") or "")
    settings["timeout"] = _whole_number(form.get("timeout"))
    # Only what the user changed meets the strict rules: a value the module
    # already holds - a factory 0.0.0.0, a name another tool set - must not
    # stop an unrelated change.
    changed = {key for key, value in settings.items() if current.get(key) != value}
    for key in changed.intersection(MODULE_SERVERS):
        _check_server(settings[key])
    if "hostname" in changed:
        _check_hostname(settings["hostname"])
    if "timeout" in changed and settings["timeout"] not in TIMEOUT_RANGE:
        raise _Refused("invalid_timeout")
    if "ip_address" in changed and settings["ip_address"] != UNSET:
        _check_server(settings["ip_address"])
    # Checked with DHCP on too: the fixed settings are written either way and
    # take effect the moment DHCP is switched off.
    if "netmask" in changed:
        _interface({**settings, "ip_address": UNSET})
    if changed & NETWORK_FIELDS and not settings["dhcp"]:
        _check_network(settings)
    return settings


def module_settings(
    form: dict[str, Any], current: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    """The settings to write, or the form error that stops them.

    Keyed and typed as the module reports them - `current` is its last read
    - so a change can be told from none.
    """
    try:
        return _settings(form, current), None
    except _Refused as refused:
        return None, refused.error

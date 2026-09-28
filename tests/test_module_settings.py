"""What the module settings form accepts, and what it hands on to write."""

import pytest

from custom_components.wago_879.module_settings import module_settings

FORM = {
    "dhcp": False,
    "ip_address": "192.0.2.4",
    "netmask": "255.255.255.0",
    "gateway": "192.0.2.1",
    "dns_server_1": "192.0.2.1",
    "dns_server_2": "",
    "ntp": True,
    "ntp_server_1": "192.0.2.1",
    "ntp_server_2": "",
    "hostname": "Wago-TCP",
    "timeout": 3000.0,
}
# What the module reported when the page opened: the form above, unchanged.
CURRENT = {
    **FORM,
    "dns_server_2": "0.0.0.0",
    "ntp_server_2": "0.0.0.0",
    "timeout": 3000,
}


def _check(changes, current=CURRENT):
    return module_settings({**FORM, **changes}, current)


def test_a_valid_form_becomes_the_values_the_module_reports():
    """Same keys and types as a read, so a change can be told from none."""
    assert module_settings(FORM, CURRENT) == (CURRENT, None)


def test_blanks_around_an_address_are_dropped():
    settings, _ = _check({"gateway": " 192.0.2.1 "})

    assert settings["gateway"] == "192.0.2.1"


def test_a_server_left_out_of_the_form_is_not_set():
    """An optional field the frontend clears is missing, not empty."""
    form = dict(FORM)
    del form["dns_server_1"]

    settings, _ = module_settings(form, CURRENT)

    assert settings["dns_server_1"] == "0.0.0.0"


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"ip_address": "192.0.2.300"}, "invalid_address"),
        ({"ntp_server_1": "time.example"}, "invalid_address"),
        ({"ip_address": "0.0.0.0"}, "invalid_address"),
        ({"ip_address": "192.0.2.0"}, "invalid_address"),
        ({"ip_address": "192.0.2.255"}, "invalid_address"),
        ({"ip_address": "127.0.0.5", "netmask": "255.0.0.0"}, "invalid_address"),
        ({"ip_address": "224.0.0.5", "gateway": ""}, "invalid_address"),
        ({"ip_address": "169.254.0.5", "gateway": ""}, "invalid_address"),
        ({"dns_server_1": "255.255.255.255"}, "invalid_address"),
        ({"ntp_server_1": "127.0.0.1"}, "invalid_address"),
        ({"ntp_server_2": "224.0.1.1"}, "invalid_address"),
        ({"netmask": "255.0.255.0"}, "invalid_netmask"),
        # Host-mask notation: Python reads it as /24, the module would get
        # the words as typed.
        ({"netmask": "0.0.0.255"}, "invalid_netmask"),
        ({"netmask": "0.0.0.0"}, "invalid_netmask"),
        ({"netmask": "255.255.255.254"}, "invalid_netmask"),
        ({"gateway": "198.51.100.1"}, "gateway_outside_network"),
        ({"gateway": "192.0.2.4"}, "gateway_outside_network"),
        ({"gateway": "192.0.2.0"}, "gateway_outside_network"),
        ({"gateway": "192.0.2.255"}, "gateway_outside_network"),
        ({"hostname": ""}, "invalid_hostname"),
        ({"hostname": "meter room"}, "invalid_hostname"),
        ({"hostname": "-meter"}, "invalid_hostname"),
        ({"hostname": "zähler"}, "invalid_hostname"),
        ({"hostname": "m" * 32}, "invalid_hostname"),
        ({"timeout": 2500.5}, "not_a_whole_number"),
        ({"timeout": "nan"}, "not_a_whole_number"),
        ({"timeout": "soon"}, "not_a_whole_number"),
    ],
)
def test_a_setting_the_module_cannot_use_is_refused(changes, error):
    assert _check(changes) == (None, error)


def test_the_longest_host_name_fits_with_room_for_its_terminator():
    settings, error = _check({"hostname": "m" * 31})

    assert error is None
    assert settings["hostname"] == "m" * 31


def test_with_dhcp_the_fixed_address_may_stay_unset():
    """A module fresh from the factory runs DHCP with no fixed address."""
    factory = {**CURRENT, "dhcp": True, "ip_address": "0.0.0.0"}

    settings, error = _check({"dhcp": True, "ip_address": ""}, factory)

    assert error is None
    assert settings["ip_address"] == "0.0.0.0"


def test_with_dhcp_the_netmask_still_has_to_be_one():
    """The fixed settings are written with DHCP on too, and take effect the
    moment it is switched off."""
    assert _check({"dhcp": True, "netmask": "255.0.255.0"}) == (
        None,
        "invalid_netmask",
    )


def test_no_gateway_is_allowed():
    settings, error = _check({"gateway": ""})

    assert error is None
    assert settings["gateway"] == "0.0.0.0"


def test_switching_dhcp_off_checks_the_fixed_address_it_falls_back_to():
    factory = {**CURRENT, "dhcp": True, "ip_address": "0.0.0.0"}

    assert _check({"ip_address": ""}, factory) == (None, "invalid_address")


def test_a_value_the_module_holds_does_not_block_another_change():
    """Only what the user changed is held to the strict rules: a name with an
    underscore set by another tool must not stop a new timeout."""
    odd = {**CURRENT, "hostname": "meter_room"}

    settings, error = _check({"hostname": "meter_room", "timeout": 2500}, odd)

    assert error is None
    assert settings["timeout"] == 2500
    assert settings["hostname"] == "meter_room"

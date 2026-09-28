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


def test_a_valid_form_becomes_the_values_the_module_reports():
    """Same keys and types as a read, so a change can be told from none."""
    settings, error = module_settings(FORM)

    assert error is None
    assert settings == {
        **FORM,
        "dns_server_2": "0.0.0.0",
        "ntp_server_2": "0.0.0.0",
        "timeout": 3000,
    }


def test_blanks_around_an_address_are_dropped():
    settings, _ = module_settings({**FORM, "gateway": " 192.0.2.1 "})

    assert settings["gateway"] == "192.0.2.1"


def test_a_server_left_out_of_the_form_is_not_set():
    """An optional field the frontend clears is missing, not empty."""
    form = dict(FORM)
    del form["dns_server_1"]

    settings, _ = module_settings(form)

    assert settings["dns_server_1"] == "0.0.0.0"


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"ip_address": "192.0.2.300"}, "invalid_address"),
        ({"ntp_server_1": "time.example"}, "invalid_address"),
        ({"ip_address": "0.0.0.0"}, "invalid_address"),
        ({"ip_address": "192.0.2.0"}, "invalid_address"),
        ({"ip_address": "192.0.2.255"}, "invalid_address"),
        ({"netmask": "255.0.255.0"}, "invalid_netmask"),
        ({"gateway": "198.51.100.1"}, "gateway_outside_network"),
        ({"gateway": "192.0.2.4"}, "gateway_outside_network"),
        ({"hostname": ""}, "invalid_hostname"),
        ({"hostname": "meter room"}, "invalid_hostname"),
        ({"hostname": "-meter"}, "invalid_hostname"),
        ({"hostname": "zähler"}, "invalid_hostname"),
        ({"hostname": "m" * 33}, "invalid_hostname"),
        ({"timeout": 2500.5}, "not_a_whole_number"),
        ({"timeout": "nan"}, "not_a_whole_number"),
        ({"timeout": "soon"}, "not_a_whole_number"),
    ],
)
def test_a_setting_the_module_cannot_use_is_refused(changes, error):
    assert module_settings({**FORM, **changes}) == (None, error)


def test_with_dhcp_the_fixed_address_only_has_to_be_an_address():
    """The module keeps the fixed address for when DHCP is switched off
    again; with DHCP on it need not fit a network yet."""
    settings, error = module_settings(
        {**FORM, "dhcp": True, "ip_address": "0.0.0.0", "gateway": "198.51.100.1"}
    )

    assert error is None
    assert settings["ip_address"] == "0.0.0.0"


def test_no_gateway_is_allowed():
    settings, error = module_settings({**FORM, "gateway": ""})

    assert error is None
    assert settings["gateway"] == "0.0.0.0"


def test_with_dhcp_the_netmask_still_has_to_be_one():
    """The fixed settings are written with DHCP on too, and take effect the
    moment it is switched off."""
    assert module_settings({**FORM, "dhcp": True, "netmask": "255.0.255.0"}) == (
        None,
        "invalid_netmask",
    )

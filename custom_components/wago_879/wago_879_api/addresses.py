"""Which IPv4 addresses a device on a LAN can be reached at."""

from __future__ import annotations

from ipaddress import IPv4Address


def is_host_address(address: IPv4Address) -> bool:
    """Whether `address` can belong to one host.

    Refuses unset, loopback, multicast, link-local and reserved addresses;
    the reserved block takes in the broadcast 255.255.255.255. Whether it is
    the network or broadcast address of its subnet needs the netmask, which
    only the caller knows.
    """
    return not (
        address.is_unspecified
        or address.is_loopback
        or address.is_multicast
        or address.is_link_local
        or address.is_reserved
    )

"""Find 879-9000 modules the way their maker's configuration tool does.

The tool broadcasts seven 0xFF bytes to UDP port 20000; every module in the
subnet answers the sender's address and port with 32 bytes. Routers do not
pass the broadcast on, so only modules in a subnet of this host are found.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from ipaddress import IPv4Address

from .addresses import is_host_address

BROWSE_PORT = 20000
BROWSE_REQUEST = b"\xff" * 7
REPLY_LENGTH = 32
# Bytes 0-5 are the MAC, 6-11 are not decoded, 16-19 the netmask.
REPLY_ADDRESS = slice(12, 16)
REPLY_SERIAL = slice(20, 32)
# A module leaves about one request in three unanswered (measured, with no
# pattern in the gaps), so a search asks several times.
BROWSE_ATTEMPTS = 3
BROWSE_GAP_SECONDS = 0.5
REPLY_WAIT_SECONDS = 1.5


@dataclass(frozen=True)
class FoundModule:
    """A module that answered a search."""

    serial_number: str
    host: str


def parse_reply(data: bytes) -> FoundModule | None:
    """The module a datagram describes, or None for anything else."""
    if len(data) != REPLY_LENGTH:
        return None
    try:
        serial = data[REPLY_SERIAL].decode("ascii")
    except UnicodeDecodeError:
        return None
    host = IPv4Address(data[REPLY_ADDRESS])
    if not serial.isalnum() or not is_host_address(host):
        return None
    return FoundModule(serial_number=serial, host=str(host))


class _Replies(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.found: dict[str, FoundModule] = {}

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        module = parse_reply(data)
        if module is not None:
            self.found[module.serial_number] = module


async def async_browse(
    targets: Iterable[str], *, port: int = BROWSE_PORT
) -> list[FoundModule]:
    """Every module that answered a search sent to the `targets` addresses.

    Raises OSError when no socket can be opened.
    """
    addresses = list(targets)
    loop = asyncio.get_running_loop()
    transport, replies = await loop.create_datagram_endpoint(
        _Replies, local_addr=("0.0.0.0", 0), allow_broadcast=True
    )
    try:
        for _ in range(BROWSE_ATTEMPTS):
            for address in addresses:
                transport.sendto(BROWSE_REQUEST, (address, port))
            await asyncio.sleep(BROWSE_GAP_SECONDS)
        await asyncio.sleep(REPLY_WAIT_SECONDS)
    finally:
        transport.close()
    return list(replies.found.values())

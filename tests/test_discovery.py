"""The search the module's configuration tool runs, and its reply.

The reply bytes are the ones a live module sent, with the MAC, the address
and the serial number replaced.
"""

import asyncio

import pytest

from custom_components.wago_879.wago_879_api import discovery
from custom_components.wago_879.wago_879_api.discovery import (
    BROWSE_REQUEST,
    FoundModule,
    async_browse,
    parse_reply,
)

REPLY = (
    bytes.fromhex(
        "00134d000001"  # MAC
        "ff4a010a0000"  # not decoded
        "c0000204"  # 192.0.2.4
        "ffffff00"  # 255.255.255.0
    )
    + b"033000000001"
)


def test_a_reply_names_the_module_and_its_address():
    assert parse_reply(REPLY) == FoundModule(
        serial_number="033000000001", host="192.0.2.4"
    )


@pytest.mark.parametrize(
    "datagram",
    [
        BROWSE_REQUEST,  # another searcher's request, heard on the same port
        REPLY[:-1],
        REPLY + b"\0",
        REPLY[:20] + b"\xff" * 12,
        REPLY[:20] + b"03300000000\0",
    ],
    ids=["request", "short", "long", "not text", "not a serial"],
)
def test_anything_but_a_module_reply_is_ignored(datagram):
    assert parse_reply(datagram) is None


class _Module(asyncio.DatagramProtocol):
    """A module that leaves every other request unanswered, as the real one
    leaves about one in three."""

    def __init__(self) -> None:
        self.requests = 0

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.requests += 1
        if data != BROWSE_REQUEST or self.requests % 2:
            return
        self.transport.sendto(b"noise", addr)
        self.transport.sendto(REPLY, addr)


@pytest.fixture
def quick(monkeypatch, socket_enabled):
    """Real sockets on the loopback, with the waits cut short."""
    monkeypatch.setattr(discovery, "BROWSE_GAP_SECONDS", 0.05)
    monkeypatch.setattr(discovery, "REPLY_WAIT_SECONDS", 0.2)


async def test_the_search_asks_again_and_reports_each_module_once(quick):
    loop = asyncio.get_running_loop()
    transport, module = await loop.create_datagram_endpoint(
        _Module, local_addr=("127.0.0.1", 0)
    )
    port = transport.get_extra_info("sockname")[1]
    try:
        found = await async_browse(["127.0.0.1"], port=port)
    finally:
        transport.close()

    assert module.requests == discovery.BROWSE_ATTEMPTS
    assert found == [FoundModule(serial_number="033000000001", host="192.0.2.4")]


async def test_a_search_nobody_answers_finds_nothing(quick):
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0)
    )
    port = transport.get_extra_info("sockname")[1]
    try:
        assert await async_browse(["127.0.0.1"], port=port) == []
    finally:
        transport.close()

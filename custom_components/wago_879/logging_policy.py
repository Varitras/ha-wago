"""What this integration may say about a meter, and how loudly.

Home Assistant logs are the standard attachment to a GitHub issue report, and
a `ConfigEntryError` message is copied into one just as readily, so the two
share this one masking function instead of each site deciding for itself.
"""

from __future__ import annotations

import logging

from modbus_connection import ModbusConnectionError

from homeassistant.helpers.update_coordinator import UpdateFailed

# The only class in modbus_connection's hierarchy that means no answer arrived
# at all: "the link is down: not connected, connection lost, or transport
# failure". A timeout is ambiguous - the meter may be answering, just late; a
# protocol error means bytes did come back; and a Modbus exception response,
# gateway codes included, means something in the path replied and can be
# wrong in a way the user has to fix (a unit id nobody serves, for one).
UNREACHABLE_ERRORS = (ModbusConnectionError,)

# Enough of the tail to tell two meters of one installation apart - WAGO
# serials are issued in sequence, so neighbouring units differ in exactly
# these characters - and too little to identify a meter from a published
# report.
IDENTIFIER_TAIL_LENGTH = 4
MASK_PREFIX = "..."


def mask(identifier: str) -> str:
    """Shorten a host or a serial number for a log line or a UI message."""
    if len(identifier) <= IDENTIFIER_TAIL_LENGTH:
        return MASK_PREFIX
    return f"{MASK_PREFIX}{identifier[-IDENTIFIER_TAIL_LENGTH:]}"


class DeviceUnreachable(UpdateFailed):
    """The meter did not answer at all - normal while it is switched off."""


class OfflineIsNotAnError(logging.Filter):
    """Rewrites the coordinator's refresh-failure record for an absent meter.

    `DataUpdateCoordinator` writes that failure at `error` with no setting to
    change it, which paints the log panel red for something the user can
    neither fix nor need fix; the quality-scale rule `log-when-unavailable`
    asks for `info`. Home Assistant formats lazily, so the exception is still
    an object in `record.args` and the case is recognisable without matching
    message text.

    Attach it to the very logger handed to the coordinator as `logger=`: a
    filter does not see the records a child logger merely propagates upwards.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Downgrade in place, never drop - other records must pass unchanged."""
        is_expected_offline = record.levelno == logging.ERROR and any(
            isinstance(argument, DeviceUnreachable) for argument in record.args or ()
        )
        if is_expected_offline:
            record.levelno = logging.INFO
            record.levelname = logging.getLevelName(logging.INFO)
        return True

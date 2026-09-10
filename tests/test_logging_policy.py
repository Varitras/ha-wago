"""Masking keeps meters apart; an absent meter is not an error."""

from custom_components.wago_879.logging_policy import (
    IDENTIFIER_TAIL_LENGTH,
    identifier_tail,
    mask,
)

SERIAL = "00123456"
HOST = "192.0.2.10"


def test_mask_keeps_only_the_tail():
    assert mask(SERIAL) == "...3456"
    assert mask(HOST) == "...2.10"


def test_the_tail_is_what_the_mask_shows_without_its_prefix():
    """The device name shows the same tail; how much is safe is decided once."""
    assert identifier_tail(SERIAL) == "3456"
    assert mask(SERIAL).endswith(identifier_tail(SERIAL))


def test_two_meters_of_one_installation_stay_distinguishable():
    """WAGO serials run in sequence, so two units of one installation differ
    in exactly the digits the mask keeps."""
    assert mask("00123456") != mask("00123457")


def test_an_identifier_no_longer_than_the_tail_reveals_nothing():
    """Keeping the tail of a short identifier would be keeping the identifier."""
    assert mask("a" * IDENTIFIER_TAIL_LENGTH) == "..."
    assert mask("") == "..."

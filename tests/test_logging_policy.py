"""Masking keeps meters apart; an absent meter is not an error."""

from custom_components.wago_879.logging_policy import IDENTIFIER_TAIL_LENGTH, mask

SERIAL = "00123456"
HOST = "192.0.2.10"


def test_mask_keeps_only_the_tail():
    assert mask(SERIAL) == "...3456"
    assert mask(HOST) == "...2.10"


def test_two_meters_of_one_installation_stay_distinguishable():
    """WAGO serials run in sequence, so two units of one installation differ
    in exactly the digits the mask keeps."""
    assert mask("00123456") != mask("00123457")


def test_an_identifier_no_longer_than_the_tail_reveals_nothing():
    """Keeping the tail of a short identifier would be keeping the identifier."""
    assert mask("a" * IDENTIFIER_TAIL_LENGTH) == "..."
    assert mask("") == "..."

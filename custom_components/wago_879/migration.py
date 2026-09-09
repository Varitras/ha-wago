"""Adopt the entity ids the YAML modbus block registered.

The thirteen legacy sensors carry unique ids on the core `modbus` platform,
so they hold their entity ids in the registry until removed - and long-term
statistics are keyed by entity id. Verified in
`homeassistant/components/recorder/entity_registry.py`: the recorder moves
statistics on an entity id *rename* only and leaves them untouched when a
registry entry is removed. So adoption is: resolve every legacy entry, remove
it, pre-register the new unique id under the same entity id. When the sensor
platform later adds the entity with that unique id, it takes the pre-registered
entity id - Home Assistant offers no public per-entity hook for a suggested
object id, which is why the registry is used directly.

What the user configured on the legacy entry travels with the entity id. The
integration's own values are re-supplied on every start, but the user's
settings would stay behind with the removed entry, so they are copied across.

All-or-nothing, and refused while a legacy entity is still live.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityStateAttribute, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .entity_descriptions import LEGACY_UNIQUE_IDS
from .logging_policy import mask

_LOGGER = logging.getLogger(__name__)

LEGACY_PLATFORM = "modbus"

# Registry fields that hold a decision of the user's rather than of the
# integration's: everything an integration supplies (`original_name`,
# `capabilities`, `translation_key` ...) is supplied again on every start,
# while these are only stored on the entry adoption removes. `disabled_by` and
# `hidden_by` are handled separately - they record *who* set them.
USER_OWNED_FIELDS = (
    "aliases",
    "area_id",
    "categories",
    "device_class",
    "icon",
    "labels",
    "name",
)


@dataclass(frozen=True)
class Adoption:
    """One legacy entity and the unique id that takes its entity id over."""

    legacy: er.RegistryEntry
    new_unique_id: str


def _user_settings(legacy: er.RegistryEntry) -> dict[str, Any]:
    """Return the user's settings on `legacy` as `async_update_entity` keywords.

    A disabled or hidden state only travels when the user chose it: the modbus
    platform's own reason to disable an entity - the config entry it belonged
    to, the device, Home Assistant itself - says nothing about the entity this
    integration creates, and this integration decides for itself which of its
    sensors start disabled.
    """
    settings: dict[str, Any] = {
        field: getattr(legacy, field) for field in USER_OWNED_FIELDS
    }
    if legacy.disabled_by is er.RegistryEntryDisabler.USER:
        settings["disabled_by"] = legacy.disabled_by
    if legacy.hidden_by is er.RegistryEntryHider.USER:
        settings["hidden_by"] = legacy.hidden_by
    return settings


def _resolve(hass: HomeAssistant, serial: str) -> list[Adoption]:
    """Return every adoption to perform, or raise before anything is changed.

    Every reason to refuse an adoption is checked here, so the caller either
    gets a list it can apply without a further decision or an exception raised
    while the registry is still untouched.
    """
    registry = er.async_get(hass)
    adoptions: list[Adoption] = []
    for legacy_unique_id, field in LEGACY_UNIQUE_IDS.items():
        # A hit here is by construction an entry of the modbus platform: the
        # lookup is keyed by (domain, platform, unique id).
        entity_id = registry.async_get_entity_id(
            Platform.SENSOR, LEGACY_PLATFORM, legacy_unique_id
        )
        if entity_id is None:
            # Never registered by the YAML block, or adopted on an earlier run.
            continue
        # Home Assistant writes an `unavailable` state carrying this marker for
        # every enabled registry entry no platform serves, once per start. The
        # documented migration - remove the block, restart, add the integration
        # - produces exactly that, so treating it as a live entity would refuse
        # the only path there is. A state without the marker is written by
        # something that is serving the entity, `unavailable` included.
        state = hass.states.get(entity_id)
        if state is not None and not state.attributes.get(
            EntityStateAttribute.RESTORED
        ):
            raise ConfigEntryNotReady(
                f"{entity_id} is still served by the YAML modbus configuration. "
                "Remove the modbus: block for this meter, restart, then set up again"
            )
        new_unique_id = f"{serial}_{field}"
        held_by = registry.async_get_entity_id(Platform.SENSOR, DOMAIN, new_unique_id)
        if held_by is not None:
            raise ConfigEntryNotReady(
                f"cannot move {entity_id} to {mask(new_unique_id)}: that unique "
                f"id is already registered as {held_by}. Remove {held_by} first"
            )
        # `async_get_or_create` restores a deleted entry's own entity id before
        # it looks at `suggested_object_id`, so a removed entry for this unique
        # id would win over the id being adopted here.
        deleted = registry.deleted_entities.get(
            (Platform.SENSOR, DOMAIN, new_unique_id)
        )
        if deleted is not None and deleted.entity_id != entity_id:
            raise ConfigEntryNotReady(
                f"cannot keep {entity_id}: a removed entry for "
                f"{mask(new_unique_id)} "
                f"would be restored as {deleted.entity_id}. Purge that removed "
                "entry before setting up this meter"
            )
        adoptions.append(Adoption(registry.entities[entity_id], new_unique_id))
    return adoptions


async def async_adopt_legacy_entities(
    hass: HomeAssistant, entry: ConfigEntry, serial: str
) -> None:
    """Move the legacy entity ids to this entry; a no-op without legacy entries."""
    adoptions = _resolve(hass, serial)
    if not adoptions:
        return
    registry = er.async_get(hass)
    for adoption in adoptions:
        legacy_entity_id = adoption.legacy.entity_id
        object_id = legacy_entity_id.split(".", 1)[1]
        registry.async_remove(legacy_entity_id)
        created = registry.async_get_or_create(
            Platform.SENSOR,
            DOMAIN,
            adoption.new_unique_id,
            suggested_object_id=object_id,
            config_entry=entry,
        )
        if created.entity_id != legacy_entity_id:
            # Unreachable through `_resolve`, kept because the alternative to
            # noticing here is silently orphaning years of statistics. Undo
            # before raising: the legacy entry is deleted rather than gone, so
            # re-creating it restores its entity id and the next attempt starts
            # from the state this one found.
            registry.async_remove(created.entity_id)
            restored = registry.async_get_or_create(
                Platform.SENSOR, LEGACY_PLATFORM, adoption.legacy.unique_id
            )
            if restored.entity_id != legacy_entity_id:
                _LOGGER.error(
                    "Statistics recorded under %s are now orphaned: the registry "
                    "gave %s to the new entity and %s to the restored legacy "
                    "entity. Rename %s back to %s to recover them",
                    legacy_entity_id,
                    created.entity_id,
                    restored.entity_id,
                    restored.entity_id,
                    legacy_entity_id,
                )
            raise ConfigEntryNotReady(
                f"could not keep {legacy_entity_id}: the registry gave "
                f"{created.entity_id}. Remove the stale entity holding that id"
            )
        registry.async_update_entity(
            created.entity_id, **_user_settings(adoption.legacy)
        )
        for options_domain, options in adoption.legacy.options.items():
            registry.async_update_entity_options(
                created.entity_id, options_domain, options
            )
        _LOGGER.info(
            "Adopted %s from the YAML modbus block as %s",
            legacy_entity_id,
            mask(adoption.new_unique_id),
        )

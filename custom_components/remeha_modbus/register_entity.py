"""Base class for entities that are backed by a register of the GTW-08 parameter list."""

import logging
from collections.abc import Callable

from aio_remeha_modbus.gtw08.system_discovery_table import DeviceBoard
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.remeha_modbus.const import DOMAIN
from custom_components.remeha_modbus.coordinator import RemehaUpdateCoordinator
from custom_components.remeha_modbus.register_map import REGISTER_DEFINITIONS
from custom_components.remeha_modbus.registers import RegisterDef

_LOGGER = logging.getLogger(__name__)


def register_definitions(
    predicate: Callable[[RegisterDef], bool],
) -> list[RegisterDef]:
    """Return all register definitions that match the given predicate."""

    return [definition for definition in REGISTER_DEFINITIONS if predicate(definition)]


class RemehaRegisterEntity(CoordinatorEntity[RemehaUpdateCoordinator]):
    """An entity whose state is a single register of the appliance.

    The register is only polled while the entity is added to Home Assistant, so entities that
    are disabled in the entity registry do not cause any modbus traffic.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: RemehaUpdateCoordinator,
        parent_device_id: int | None,
        definition: RegisterDef,
    ):
        """Create a new register entity."""

        super().__init__(coordinator=coordinator)

        self._definition = definition
        self._parent_device_id = parent_device_id

        self._attr_name = definition.name
        self._attr_unique_id = f"{DOMAIN}_{definition.unique_key}"
        self._attr_entity_registry_enabled_default = definition.enabled_by_default
        self._attr_entity_category = (
            EntityCategory.CONFIG if definition.writable else EntityCategory.DIAGNOSTIC
        )
        self._attr_extra_state_attributes = {
            "register": definition.register,
            "group": definition.group,
        }

    async def async_added_to_hass(self) -> None:
        """Start polling the register of this entity."""

        await super().async_added_to_hass()

        self.async_on_remove(self.coordinator.subscribe_register(self._definition))

        # Don't make the user wait for the next scheduled update to see a value.
        if self.coordinator.get_register_value(self._definition.register) is None:
            await self.coordinator.async_request_refresh()

    @property
    def _raw_value(self) -> float | int | None:
        return self.coordinator.get_register_value(self._definition.register)

    @property
    def available(self) -> bool:
        """Return whether the register could be read."""

        return super().available and self._raw_value is not None

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return information about the device this entity belongs to."""

        if self._parent_device_id is None:
            return None

        board: DeviceBoard | None = self.coordinator.get_device(id=self._parent_device_id)
        if board is None or board.hardware_version is None or board.software_version is None:
            return None

        return DeviceInfo(
            identifiers={(DOMAIN, str(board.article_number))},
            hw_version=f"HW{board.hardware_version[0]:02d}.{board.hardware_version[1]:02d}",
            manufacturer="Remeha",
            model=str(board.board_category),
            sw_version=f"SW{board.software_version[0]:02d}.{board.software_version[1]:02d}",
        )

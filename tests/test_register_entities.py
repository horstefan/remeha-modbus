# ruff: noqa: SLF001
"""Tests for the entities that are backed by the GTW-08 parameter list."""

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import RegistryEntryDisabler

from custom_components.remeha_modbus.const import DOMAIN
from custom_components.remeha_modbus.coordinator import RemehaUpdateCoordinator
from custom_components.remeha_modbus.register_map import REGISTER_DEFINITIONS

from .conftest import setup_platform

FLUE_GAS_TEMPERATURE = 402
HYBRID_MODE = 464
BACKUP_START_DELAY = 488
SPECIAL_STATUS = 276


def _definition(register: int):
    return next(d for d in REGISTER_DEFINITIONS if d.register == register)


async def _setup(hass: HomeAssistant, remeha_api, mock_config_entry, remeha_modbus_unit):
    with patch(
        "aio_remeha_modbus.gtw08.GTW08",
        new=lambda *args, **kwargs: remeha_api,
    ):
        await setup_platform(
            hass=hass, config_entry=mock_config_entry, remeha_modbus_unit=remeha_modbus_unit
        )
        await hass.async_block_till_done()


def _find_entity_id(hass: HomeAssistant, platform: str, register: int, bit: int | None = None):
    suffix = f"_bit{bit}" if bit is not None else ""
    return er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{DOMAIN}_register_{register}{suffix}"
    )


async def test_enabled_sensor_reads_register(
    hass: HomeAssistant, remeha_api, mock_config_entry, remeha_modbus_unit
):
    """Test that an enabled sensor polls its register and applies the scale."""

    remeha_modbus_unit.load_raw({"holding": {FLUE_GAS_TEMPERATURE: 6543}})
    await _setup(hass, remeha_api, mock_config_entry, remeha_modbus_unit)

    entity_id = _find_entity_id(hass, "sensor", FLUE_GAS_TEMPERATURE)
    assert entity_id is not None

    coordinator: RemehaUpdateCoordinator = mock_config_entry.runtime_data["coordinator"]
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state is not None
    assert float(state.state) == pytest.approx(65.43)
    assert state.attributes["unit_of_measurement"] == "°C"


async def test_disabled_entities_are_not_polled(
    hass: HomeAssistant, remeha_api, mock_config_entry, remeha_modbus_unit
):
    """Test that entities that are disabled by default are registered but not polled."""

    await _setup(hass, remeha_api, mock_config_entry, remeha_modbus_unit)

    coordinator: RemehaUpdateCoordinator = mock_config_entry.runtime_data["coordinator"]
    registry = er.async_get(hass)

    for platform, register in (("select", HYBRID_MODE), ("number", BACKUP_START_DELAY)):
        entity_id = _find_entity_id(hass, platform, register)
        assert entity_id is not None
        entry = registry.async_get(entity_id)
        assert entry is not None
        assert entry.disabled_by is RegistryEntryDisabler.INTEGRATION
        assert hass.states.get(entity_id) is None

    assert HYBRID_MODE not in coordinator._register_subscriptions
    assert FLUE_GAS_TEMPERATURE in coordinator._register_subscriptions


async def test_write_register(
    hass: HomeAssistant, remeha_api, mock_config_entry, remeha_modbus_unit
):
    """Test that writing a register encodes the value and updates the cached value."""

    await _setup(hass, remeha_api, mock_config_entry, remeha_modbus_unit)

    coordinator: RemehaUpdateCoordinator = mock_config_entry.runtime_data["coordinator"]

    await coordinator.async_write_register(_definition(BACKUP_START_DELAY), 25)
    assert remeha_modbus_unit.holding[BACKUP_START_DELAY] == 25

    await coordinator.async_write_register(_definition(HYBRID_MODE), 2)
    assert remeha_modbus_unit.holding[HYBRID_MODE] == 2
    assert coordinator.data["registers"][HYBRID_MODE] == 2

    with pytest.raises(ValueError):
        await coordinator.async_write_register(_definition(BACKUP_START_DELAY), 70000)

    with pytest.raises(ValueError):
        await coordinator.async_write_register(_definition(FLUE_GAS_TEMPERATURE), 10)


async def test_subscribe_register_unsubscribe(
    hass: HomeAssistant, remeha_api, mock_config_entry, remeha_modbus_unit
):
    """Test that a register is polled until the last subscriber unsubscribes."""

    await _setup(hass, remeha_api, mock_config_entry, remeha_modbus_unit)

    coordinator: RemehaUpdateCoordinator = mock_config_entry.runtime_data["coordinator"]
    definition = _definition(HYBRID_MODE)
    remeha_modbus_unit.load_raw({"holding": {HYBRID_MODE: 3}})

    first = coordinator.subscribe_register(definition)
    second = coordinator.subscribe_register(definition)

    await coordinator.async_refresh()
    assert coordinator.get_register_value(HYBRID_MODE) == 3

    first()
    first()  # unsubscribing twice has no effect
    assert HYBRID_MODE in coordinator._register_subscriptions

    second()
    assert HYBRID_MODE not in coordinator._register_subscriptions

    await coordinator.async_refresh()
    assert coordinator.get_register_value(HYBRID_MODE) is None


async def test_bit_field_binary_sensors(
    hass: HomeAssistant, remeha_api, mock_config_entry, remeha_modbus_unit
):
    """Test that a bit field register is exposed as one binary sensor per bit."""

    await _setup(hass, remeha_api, mock_config_entry, remeha_modbus_unit)

    definition = _definition(SPECIAL_STATUS)
    assert definition.bits is not None

    for bit in range(len(definition.bits)):
        assert _find_entity_id(hass, "binary_sensor", SPECIAL_STATUS, bit) is not None

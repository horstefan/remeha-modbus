"""Tests for the register description, decoding and encoding."""

import itertools

import pytest

from custom_components.remeha_modbus.register_map import REGISTER_DEFINITIONS
from custom_components.remeha_modbus.registers import RegisterDef, decode_run, plan_reads


def _reg(register: int, data_type: str, **kwargs) -> RegisterDef:
    return RegisterDef(register=register, group="test", name="test", data_type=data_type, **kwargs)


@pytest.mark.parametrize(
    ("definition", "words", "expected"),
    [
        (_reg(1, "UINT8"), [0x0102], 2),  # only the lower byte counts
        (_reg(1, "INT8"), [0x00FE], -2),
        (_reg(1, "UINT16"), [0x1234], 0x1234),
        (_reg(1, "UINT16"), [0xFFFF], None),  # not available
        (_reg(1, "INT16", scale=0.01), [0xFFFF], -0.01),
        (_reg(1, "INT16", scale=0.01), [2150], 21.5),
        (_reg(1, "UINT32"), [0x0001, 0x0002], 0x10002),
        (_reg(1, "UINT32"), [0xFFFF, 0xFFFF], None),
        (_reg(1, "UINT8", enum={0: "a", 1: "b"}), [1], 1),
    ],
)
def test_decode(definition: RegisterDef, words: list[int], expected):
    """Test decoding raw words."""

    assert definition.decode(words) == expected


def test_decode_wrong_word_count():
    """Test that decoding the wrong amount of words fails."""

    with pytest.raises(ValueError):
        _reg(1, "UINT32").decode([1])


@pytest.mark.parametrize(
    ("definition", "value", "expected"),
    [
        (_reg(1, "UINT8"), 5, [5]),
        (_reg(1, "INT8"), -2, [0xFE]),
        (_reg(1, "INT16", scale=0.01), 21.5, [2150]),
        (_reg(1, "INT16", scale=0.01), -0.01, [0xFFFF]),
        (_reg(1, "UINT32"), 0x10002, [1, 2]),
    ],
)
def test_encode(definition: RegisterDef, value: float, expected: list[int]):
    """Test encoding values."""

    assert definition.encode(value) == expected


@pytest.mark.parametrize(
    ("definition", "value"),
    [
        (_reg(1, "UINT8"), 256),
        (_reg(1, "UINT8"), -1),
        (_reg(1, "UINT16"), 0xFFFF),  # reserved for 'not available'
        (_reg(1, "INT8"), 128),
    ],
)
def test_encode_out_of_range(definition: RegisterDef, value: float):
    """Test that out-of-range values are rejected."""

    with pytest.raises(ValueError):
        definition.encode(value)


@pytest.mark.parametrize("definition", REGISTER_DEFINITIONS, ids=lambda d: str(d.register))
def test_encode_decode_roundtrip(definition: RegisterDef):
    """Test that every defined register survives an encode/decode round trip."""

    value = definition.native_min
    assert definition.decode(definition.encode(value)) == pytest.approx(value)


def test_definitions_are_consistent():
    """Test that the generated register map is sane."""

    registers = [d.register for d in REGISTER_DEFINITIONS]
    assert len(registers) == len(set(registers))

    # No two definitions may overlap.
    spans = sorted((d.register, d.register + d.word_count) for d in REGISTER_DEFINITIONS)
    for (_, previous_end), (start, _) in itertools.pairwise(spans):
        assert previous_end <= start

    for definition in REGISTER_DEFINITIONS:
        assert definition.scale > 0
        assert not (definition.bits and definition.writable)
        if definition.enum:
            assert len(set(definition.enum.values())) == len(definition.enum)


def test_plan_reads_groups_contiguous_registers():
    """Test that adjacent registers are combined and gaps are not read."""

    definitions = [
        _reg(10, "UINT16"),
        _reg(11, "UINT32"),
        _reg(13, "UINT8"),
        _reg(20, "UINT16"),
        _reg(21, "UINT16"),
    ]

    plan = plan_reads(definitions, max_span=40)

    assert [(start, count) for start, count, _ in plan] == [(10, 4), (20, 2)]


def test_plan_reads_respects_max_span():
    """Test that a run is split when it exceeds the maximum span."""

    definitions = [_reg(address, "UINT16") for address in range(100, 105)]

    plan = plan_reads(definitions, max_span=2)

    assert [(start, count) for start, count, _ in plan] == [(100, 2), (102, 2), (104, 1)]


def test_decode_run():
    """Test decoding several registers from one read result."""

    members = [_reg(10, "UINT16"), _reg(11, "UINT32"), _reg(13, "INT8")]

    assert decode_run(10, [7, 0, 9, 0x00FF], members) == {10: 7, 11: 9, 13: -1}

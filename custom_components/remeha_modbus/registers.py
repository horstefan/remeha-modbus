"""Generic description, decoding and encoding of GTW-08 modbus registers.

This module intentionally has no Home Assistant dependencies, so it can be unit tested in isolation.

The GTW-08 exposes all values through 16-bit holding registers:

* `UINT8`/`INT8`/`ENUM8` values live in the lower byte of a single register.
* `UINT16`/`INT16` values occupy a single register.
* `UINT32`/`INT32` values occupy two consecutive registers, most significant word first.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

type DataType = Literal["UINT8", "INT8", "UINT16", "INT16", "UINT32", "INT32"]

_WORD_COUNT: Final[dict[str, int]] = {
    "UINT8": 1,
    "INT8": 1,
    "UINT16": 1,
    "INT16": 1,
    "UINT32": 2,
    "INT32": 2,
}

_BIT_WIDTH: Final[dict[str, int]] = {
    "UINT8": 8,
    "INT8": 8,
    "UINT16": 16,
    "INT16": 16,
    "UINT32": 32,
    "INT32": 32,
}

_SIGNED: Final[frozenset[str]] = frozenset({"INT8", "INT16", "INT32"})

# Raw values the appliance uses to signal 'not available'.
_NOT_AVAILABLE: Final[dict[str, int]] = {
    "UINT16": 0xFFFF,
    "UINT32": 0xFFFFFFFF,
}


@dataclass(frozen=True, slots=True)
class RegisterDef:
    """Describe a single (group of) modbus register(s)."""

    register: int
    """The address of the first register."""

    group: str
    """The sheet of the parameter list this register belongs to."""

    name: str
    """A human-readable (English) name."""

    data_type: DataType
    """The raw data type."""

    scale: float = 1
    """The factor to multiply the raw value with to get the value in `unit`."""

    unit: str | None = None
    """The unit of measurement of the scaled value, if any."""

    writable: bool = False
    """Whether the register can be written."""

    enum: dict[int, str] | None = None
    """If set, the raw value is an enum. Maps raw value to a lowercase option key."""

    bits: tuple[str, ...] | None = None
    """If set, the raw value is a bit field. Contains the lowercase name of each bit, LSB first."""

    enabled_by_default: bool = False
    """Whether the entity is enabled when first added to Home Assistant."""

    description: str = ""
    """The description from the parameter list."""

    since: str | None = None
    """The GTW-08 version that introduced this register."""

    @property
    def word_count(self) -> int:
        """Return the number of 16-bit registers this definition spans."""

        return _WORD_COUNT[self.data_type]

    @property
    def unique_key(self) -> str:
        """Return a key that is unique within the integration."""

        return f"register_{self.register}"

    @property
    def raw_min(self) -> int:
        """Return the smallest raw value that fits the data type."""

        width = _BIT_WIDTH[self.data_type]
        return -(2 ** (width - 1)) if self.data_type in _SIGNED else 0

    @property
    def raw_max(self) -> int:
        """Return the largest raw value that fits the data type."""

        width = _BIT_WIDTH[self.data_type]
        # The all-ones value of unsigned 16/32 bit types means 'not available'.
        if self.data_type in _NOT_AVAILABLE:
            return _NOT_AVAILABLE[self.data_type] - 1
        return 2 ** (width - 1) - 1 if self.data_type in _SIGNED else 2**width - 1

    @property
    def native_min(self) -> float:
        """Return the smallest scaled value that can be written."""

        return round(self.raw_min * self.scale, 6)

    @property
    def native_max(self) -> float:
        """Return the largest scaled value that can be written."""

        return round(self.raw_max * self.scale, 6)

    @property
    def native_step(self) -> float:
        """Return the smallest scaled step."""

        return self.scale

    def decode(self, words: Sequence[int]) -> int | float | None:
        """Decode raw register words to a (scaled) value.

        Args:
            words: The raw 16-bit register values, exactly `word_count` of them.

        Returns:
            The scaled value, the raw value for enums and bit fields, or `None` if the appliance
            reports the value as not available.

        """

        if len(words) != self.word_count:
            raise ValueError(
                f"Register {self.register} spans {self.word_count} word(s), got {len(words)}."
            )

        raw = 0
        for word in words:
            raw = (raw << 16) | (word & 0xFFFF)

        if self.data_type in ("UINT8", "INT8"):
            raw &= 0xFF

        if raw == _NOT_AVAILABLE.get(self.data_type):
            return None

        if self.data_type in _SIGNED:
            width = _BIT_WIDTH[self.data_type]
            if raw >= 2 ** (width - 1):
                raw -= 2**width

        if self.enum is not None or self.bits is not None:
            return raw

        if self.scale == 1:
            return raw

        return round(raw * self.scale, 6)

    def encode(self, value: float) -> list[int]:
        """Encode a (scaled) value to raw register words.

        Raises:
            ValueError: if the value does not fit the data type of this register.

        """

        raw = round(value / self.scale) if self.scale != 1 else round(value)
        if raw < self.raw_min or raw > self.raw_max:
            raise ValueError(
                f"Value {value} is out of range for register {self.register} "
                f"({self.native_min} to {self.native_max})."
            )

        width = _BIT_WIDTH[self.data_type]
        raw &= (1 << width) - 1

        count = self.word_count
        return [(raw >> (16 * (count - 1 - i))) & 0xFFFF for i in range(count)]


def plan_reads(
    definitions: Sequence[RegisterDef], *, max_span: int
) -> list[tuple[int, int, list[RegisterDef]]]:
    """Group register definitions into contiguous read requests.

    Registers that overlap or directly follow each other are read in a single request. Gaps are
    never read, since unassigned registers may be rejected by the appliance.

    Args:
        definitions: The register definitions to read.
        max_span: The maximum amount of registers in a single request.

    Returns:
        A list of `(start_address, count, definitions)` tuples, ordered by start address.

    """

    runs: list[tuple[int, int, list[RegisterDef]]] = []
    for definition in sorted(definitions, key=lambda d: d.register):
        end = definition.register + definition.word_count
        if runs:
            start, count, members = runs[-1]
            run_end = start + count
            if definition.register <= run_end and end - start <= max_span:
                members.append(definition)
                runs[-1] = (start, max(run_end, end) - start, members)
                continue
        runs.append((definition.register, definition.word_count, [definition]))

    return runs


def decode_run(
    start: int, words: Sequence[int], members: Sequence[RegisterDef]
) -> dict[int, int | float | None]:
    """Decode all `members` out of the `words` that were read starting at `start`."""

    return {
        member.register: member.decode(
            words[member.register - start : member.register - start + member.word_count]
        )
        for member in members
    }

"""Minimal protobuf wire helpers for the extended local commands.

The generic decoder (blackboxprotobuf) guesses types and loses float32 values,
and its encoder drops tags missing from the typedef. Map edits, point
navigation and config writes need exact wire types (float32 points, unwrapped
oneofs, raw sub-messages copied byte-for-byte), so these helpers work on the
wire format directly.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable, Iterator

WIRE_VARINT = 0
WIRE_FIXED64 = 1
WIRE_LEN = 2
WIRE_FIXED32 = 5


def varint(value: int) -> bytes:
    """Encode an int as a protobuf varint (negative ints as 10-byte two's complement)."""
    if value < 0:
        value += 1 << 64
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _key(field: int, wire: int) -> bytes:
    return varint((field << 3) | wire)


def f_int(field: int, value: int | bool) -> bytes:
    """Varint field (ints, enums, bools)."""
    return _key(field, WIRE_VARINT) + varint(int(value))


def f_float(field: int, value: float) -> bytes:
    """float32 field (fixed32)."""
    return _key(field, WIRE_FIXED32) + struct.pack("<f", float(value))


def f_bytes(field: int, value: bytes | str) -> bytes:
    """Length-delimited field (sub-message, string or bytes)."""
    if isinstance(value, str):
        value = value.encode()
    return _key(field, WIRE_LEN) + varint(len(value)) + value


f_msg = f_bytes


def f_packed(field: int, values: Iterable[int]) -> bytes:
    """Packed repeated varint field."""
    return f_bytes(field, b"".join(varint(int(v)) for v in values))


def point(x: float, y: float) -> bytes:
    """Point{1 x float, 2 y float} body."""
    return f_float(1, x) + f_float(2, y)


def grid_point(x: int, y: int) -> bytes:
    """Grid / GridPoint{1 x int32, 2 y int32} body."""
    return f_int(1, x) + f_int(2, y)


def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Read a varint at pos; return (value, new_pos)."""
    shift = 0
    result = 0
    while True:
        if pos >= len(data):
            raise ValueError("truncated varint")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def iter_fields(data: bytes) -> Iterator[tuple[int, int, int | bytes, bytes]]:
    """Yield (field, wire_type, value, raw_entry) for each top-level field.

    value is an int for varint, raw bytes for length-delimited, and the raw
    4/8 bytes for fixed32/fixed64. raw_entry is the complete encoded field
    (key included), so callers can copy fields unchanged.
    """
    pos = 0
    while pos < len(data):
        start = pos
        key, pos = read_varint(data, pos)
        field, wire = key >> 3, key & 7
        value: int | bytes
        if wire == WIRE_VARINT:
            value, pos = read_varint(data, pos)
        elif wire == WIRE_LEN:
            length, pos = read_varint(data, pos)
            value = data[pos : pos + length]
            pos += length
        elif wire == WIRE_FIXED32:
            value = data[pos : pos + 4]
            pos += 4
        elif wire == WIRE_FIXED64:
            value = data[pos : pos + 8]
            pos += 8
        else:
            raise ValueError(f"unsupported wire type {wire}")
        yield field, wire, value, data[start:pos]


def first(data: bytes, field: int) -> int | bytes | None:
    """Value of the first occurrence of field, or None."""
    for num, _wire, value, _raw in iter_fields(data):
        if num == field:
            return value
    return None


def all_values(data: bytes, field: int) -> list[int | bytes]:
    """Values of every occurrence of field."""
    return [value for num, _w, value, _r in iter_fields(data) if num == field]


def as_float(value: int | bytes | None) -> float | None:
    """Decode a fixed32 value as float32."""
    if isinstance(value, bytes) and len(value) == 4:
        return struct.unpack("<f", value)[0]
    return None


def as_double(value: int | bytes | None) -> float | None:
    """Decode a fixed64 value as double."""
    if isinstance(value, bytes) and len(value) == 8:
        return struct.unpack("<d", value)[0]
    return None


def as_signed(value: int | None) -> int | None:
    """Interpret a varint as a signed int64."""
    if value is None:
        return None
    return value - (1 << 64) if value >= 1 << 63 else value


def replace_field(message: bytes, field: int, entry: bytes | None) -> bytes:
    """Return message with every occurrence of field replaced by entry (or removed)."""
    parts = [(num, raw) for num, _w, _v, raw in iter_fields(message) if num != field]
    if entry:
        parts.append((field, entry))
    parts.sort(key=lambda part: part[0])
    return b"".join(raw for _num, raw in parts)

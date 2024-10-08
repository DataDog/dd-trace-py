"""
NOTE: BETA - this API is currently in development and is subject to change.
"""
import typing as t


def _bit_count_compat(_byte: int) -> int:
    return bin(_byte).count("1")


_bit_count: t.Callable[[int], int] = int.bit_count if hasattr(int, "bit_count") else _bit_count_compat


class CoverageLines:
    def __init__(self, initial_size: int = 32):
        # Initial size of 32 chosen based on p50 length of files in code base being 240 at time of writing
        self._lines = bytearray(initial_size)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CoverageLines):
            return NotImplemented
        return self._lines == other._lines

    def __len__(self):
        return self._num_lines()

    def __bool__(self):
        return self._num_lines() > 0

    def __copy__(self):
        new_instance = CoverageLines()
        new_instance._lines = self._lines.copy()
        return new_instance

    def __repr__(self):
        return f"CoverageLines(num_lines={self._num_lines()})"

    def _num_lines(self) -> int:
        return sum(_bit_count(byte) for byte in self._lines)

    def add(self, line_number: int):
        lines_byte = line_number // 8

        if lines_byte >= len(self._lines):
            self._lines.extend(bytearray(lines_byte - len(self._lines) + 1))

        # DEV this fun bit allows us to trick ourselves into little-endianness, which is what the backend wants to see
        # in bytes
        lines_bit = 0b1000_0000 >> (line_number % 8)
        self._lines[lines_byte] |= lines_bit

    def to_sorted_list(self) -> t.List[int]:
        """Returns a sorted list of covered line numbers"""
        lines = []
        for idx, _byte in enumerate(self._lines):
            for _bit in range(8):
                # Producing a list of lines needs to account for the fact they are kept in a little-endian way
                if _byte & (0b1000_0000 >> _bit):
                    lines.append(idx * 8 + _bit)

        return lines

    def update(self, other: "CoverageLines"):
        # Extend our lines if the other coverage has more lines
        if len(other._lines) > len(self._lines):
            self._lines.extend(bytearray(len(other._lines) - len(self._lines)))

        for _byte_idx, _byte in enumerate(other._lines):
            self._lines[_byte_idx] |= _byte

    def to_bytes(self) -> bytes:
        """This exists as a simple interface in case we ever decide to change the internal lines representation"""
        return self._lines

    @classmethod
    def from_list(cls, lines: t.List[int]) -> "CoverageLines":
        coverage = cls()
        for line in lines:
            coverage.add(line)
        return coverage

    @classmethod
    def from_bytearray(cls, lines: bytearray) -> "CoverageLines":
        coverage = cls()
        coverage._lines = lines
        return coverage

class CoverageLines:
    def __init__(self, initial_size: int = 8):
        # Initial size of 8 chosen based on p50 length of files in code base being 240 at time of writing
        self._lines = bytearray(initial_size)

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
        return sum([bin(byte).count("1") for byte in self._lines])

    def add(self, line_number: int):
        lines_byte = line_number // 8
        lines_bit = 2 ** (line_number % 8)

        if lines_byte >= len(self._lines):
            self._lines.extend(bytearray(lines_byte - len(self._lines) + 1))

        self._lines[lines_byte] |= lines_bit

    def add_lines(self, lines: list[int]):
        for line in lines:
            self.add(line)

    def to_string(self) -> str:
        return "".join([str(n) for n in self.to_list()])

    def to_list(self) -> list[int]:
        lines = []
        for _byte in self._lines:
            for _bit in range(8):
                if _byte & (1 << _bit):
                    lines.append(1)
                else:
                    lines.append(0)

        return lines

    def update(self, other: "CoverageLines"):
        # Extend our lines if the other coverage has more lines
        if len(other._lines) > len(self._lines):
            self._lines.extend(bytearray(len(other._lines) - len(self._lines)))

        for _byte_idx, _byte in enumerate(other._lines):
            self._lines[_byte_idx] |= _byte

    @classmethod
    def merge(cls, cov_lines_1: "CoverageLines", cov_lines_2: "CoverageLines") -> "CoverageLines":
        merged_lines = CoverageLines(max(len(cov_lines_1._lines), len(cov_lines_2._lines)))
        merged_lines.update(cov_lines_1)
        merged_lines.update(cov_lines_2)

        return merged_lines

    def to_bytes(self) -> bytes:
        return bytes(self._lines)

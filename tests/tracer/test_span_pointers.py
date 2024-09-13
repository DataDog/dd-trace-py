from typing import List
from typing import NamedTuple

import pytest

from ddtrace._trace._span_pointers import _standard_hashing_function


class TestStandardHashingFunction:
    class HashingCase(NamedTuple):
        name: str
        elements: List[bytes]
        result: str

    @pytest.mark.parametrize(
        "test_case",
        [
            HashingCase(
                name="one foo",
                elements=[b"foo"],
                result="2c26b46b68ffc68ff99b453c1d304134",
            ),
            HashingCase(
                name="foo and bar",
                elements=[b"foo", b"bar"],
                result="0fc7e25e075c7849f89b9729d1aeada1",
            ),
            HashingCase(
                name="bar and foo, order matters",
                elements=[b"bar", b"foo"],
                result="527f4b4996d63db7053fd4b376b782a3",
            ),
        ],
        ids=lambda test_case: test_case.name,
    )
    def test_normal_hashing(self, test_case: HashingCase) -> None:
        assert _standard_hashing_function(*test_case.elements) == test_case.result

    def test_validate_hashing_rules(self) -> None:
        hex_digits_per_byte = 2
        desired_bytes = _standard_hashing_function.hex_digits // hex_digits_per_byte

        bytes_in_digest = _standard_hashing_function.base_hashing_function().digest_size

        assert bytes_in_digest >= desired_bytes

    def test_hashing_requries_arguments(self) -> None:
        with pytest.raises(ValueError, match="elements must not be empty"):
            _standard_hashing_function()

    def test_hashing_requires_bytes(self) -> None:
        with pytest.raises(TypeError, match="expected a bytes-like object"):
            _standard_hashing_function("foo")

from hashlib import sha256
from typing import List
from typing import NamedTuple

import pytest

from ddtrace._trace._span_pointer import _STANDARD_HASHING_FUNCTION_FAILURE_PREFIX
from ddtrace._trace._span_pointer import _standard_hashing_function


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

    def test_validate_hash_size(self) -> None:
        bits_per_hex_digit = 4
        desired_bits = 128

        expected_hex_digits = desired_bits // bits_per_hex_digit

        assert len(_standard_hashing_function(b"foo")) == expected_hex_digits
        assert len(_standard_hashing_function("bad-input")) == expected_hex_digits

    def test_validate_using_sha256_with_pipe_separator(self) -> None:
        # We want this test to break if we change the logic of the standard
        # function in any interesting way. If we want to change this behavior
        # in the future, we'll need to make a new version of the standard
        # hashing function.

        bits_per_hex_digit = 4
        desired_bits = 128

        hex_digits = desired_bits // bits_per_hex_digit

        assert _standard_hashing_function(b"foo", b"bar") == sha256(b"foo|bar").hexdigest()[:hex_digits]

    def test_hashing_requries_arguments(self) -> None:
        assert _standard_hashing_function().startswith(_STANDARD_HASHING_FUNCTION_FAILURE_PREFIX)

    def test_hashing_requires_bytes(self) -> None:
        assert _standard_hashing_function("foo").startswith(_STANDARD_HASHING_FUNCTION_FAILURE_PREFIX)

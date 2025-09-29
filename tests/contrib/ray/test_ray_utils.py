from ddtrace.contrib.internal.ray.constants import RAY_METADATA_PREFIX
from ddtrace.contrib.internal.ray.utils import get_dd_job_name_from_entrypoint
from ddtrace.contrib.internal.ray.utils import json_to_dot_notation


def test_get_dd_job_name_from_entrypoint():
    assert get_dd_job_name_from_entrypoint("python hello.py") == "hello"
    assert get_dd_job_name_from_entrypoint("python3 hello.py") == "hello"
    assert get_dd_job_name_from_entrypoint("/Users/bits/.pyenv/shims/python3 woof.py") == "woof"
    assert get_dd_job_name_from_entrypoint("python3 woof.py --breed mutt") == "woof"
    assert get_dd_job_name_from_entrypoint("perl meow.pl") is None


def test_json_to_dot_notation():
    assert json_to_dot_notation({"a": {"b": {"c": 1}}}) == {f"{RAY_METADATA_PREFIX}.a.b.c": 1}
    assert json_to_dot_notation({"a": [1, 2, 3]}) == {f"{RAY_METADATA_PREFIX}.a": "[1, 2, 3]"}
    assert json_to_dot_notation({"a": {"b": 1}, "c": 2}) == {
        f"{RAY_METADATA_PREFIX}.a.b": 1,
        f"{RAY_METADATA_PREFIX}.c": 2,
    }
    assert json_to_dot_notation({"a": {"b": {"c": 1, "d": [1, 2]}}}) == {
        f"{RAY_METADATA_PREFIX}.a.b.c: 1, {RAY_METADATA_PREFIX}.a.b.d: [1, 2]"
    }
    assert json_to_dot_notation(1) == f"{RAY_METADATA_PREFIX}:1"

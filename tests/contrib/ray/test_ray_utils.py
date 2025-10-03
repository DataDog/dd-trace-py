from ddtrace.contrib.internal.ray.constants import RAY_METADATA_PREFIX
from ddtrace.contrib.internal.ray.constants import REDACTED_PATH
from ddtrace.contrib.internal.ray.utils import flatten_metadata_dict
from ddtrace.contrib.internal.ray.utils import get_dd_job_name_from_entrypoint
from ddtrace.contrib.internal.ray.utils import redact_paths


def test_get_dd_job_name_from_entrypoint():
    assert get_dd_job_name_from_entrypoint("python hello.py") == "hello"
    assert get_dd_job_name_from_entrypoint("python3 hello.py") == "hello"
    assert get_dd_job_name_from_entrypoint("/Users/bits/.pyenv/shims/python3 woof.py") == "woof"
    assert get_dd_job_name_from_entrypoint("python3 woof.py --breed mutt") == "woof"
    assert get_dd_job_name_from_entrypoint("perl meow.pl") is None


def test_redact_paths():
    assert redact_paths("") == ""
    assert redact_paths("my_script.py") == "my_script.py"
    assert redact_paths("python my_script.py") == "python my_script.py"
    assert redact_paths("python my_script.py arg1") == "python my_script.py arg1"
    assert redact_paths("python my_script.py arg1 --kwarg1 value1") == "python my_script.py arg1 --kwarg1 value1"
    assert redact_paths("python path/to/my_script.py") == f"python {REDACTED_PATH}/my_script.py"
    assert redact_paths("python /path/to/my_script.py") == f"python {REDACTED_PATH}/my_script.py"
    assert (
        redact_paths("path1/to1/python path2/to2/my_script.py")
        == f"{REDACTED_PATH}/python {REDACTED_PATH}/my_script.py"
    )
    assert (
        redact_paths("/path1/to1/python /path2/to2/my_script.py")
        == f"{REDACTED_PATH}/python {REDACTED_PATH}/my_script.py"
    )
    assert (
        redact_paths("/path1/to1/python /path2/to2/my_script.py /pathlike/arg1")
        == f"{REDACTED_PATH}/python {REDACTED_PATH}/my_script.py {REDACTED_PATH}/arg1"
    )
    assert (
        redact_paths("/path1/to1/python /path2/to2/my_script.py /pathlike/arg1 --kwarg1 /pathlike/value1")
        == f"{REDACTED_PATH}/python {REDACTED_PATH}/my_script.py {REDACTED_PATH}/arg1 --kwarg1 {REDACTED_PATH}/value1"
    )
    assert (
        redact_paths("/path1/to1/python /path2/to2/my_script.py /pathlike/arg1 --kwarg1=/pathlike/value1")
        == f"{REDACTED_PATH}/python {REDACTED_PATH}/my_script.py {REDACTED_PATH}/arg1 --kwarg1={REDACTED_PATH}/value1"
    )
    assert (
        redact_paths("/path1/to1/python /path2/to2/my_script.py /pathlike/arg1 --kwarg1='/pathlike/value1'")
        == f"{REDACTED_PATH}/python {REDACTED_PATH}/my_script.py {REDACTED_PATH}/arg1 --kwarg1='{REDACTED_PATH}/value1'"
    )


def test_flatten_metadata_dict():
    assert flatten_metadata_dict({"a": {"b": {"c": 1}}}) == {f"{RAY_METADATA_PREFIX}.a.b.c": 1}
    assert flatten_metadata_dict({"a": [1, 2, 3]}) == {f"{RAY_METADATA_PREFIX}.a": "[1, 2, 3]"}
    assert flatten_metadata_dict({"a": {"b": 1}, "c": 2}) == {
        f"{RAY_METADATA_PREFIX}.a.b": 1,
        f"{RAY_METADATA_PREFIX}.c": 2,
    }
    assert flatten_metadata_dict({"a": {"b": {"c": 1, "d": [1, 2]}}}) == {
        f"{RAY_METADATA_PREFIX}.a.b.c": 1,
        f"{RAY_METADATA_PREFIX}.a.b.d": "[1, 2]",
    }
    assert flatten_metadata_dict(1) == {}
    assert flatten_metadata_dict([1, 2, 3]) == {}

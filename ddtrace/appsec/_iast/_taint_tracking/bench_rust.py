import native_rust
import subprocess
import textwrap

def set_and_get_ranges(s: str):
    print(dir(native_rust))
    source = native_rust.Source("some_source", s, native_rust.OriginType.Parameter)
    range_ = native_rust.TaintRange(0, 3, source)
    native_rust.set_ranges(s, [range_])
    ranges = native_rust.get_ranges(s)
    assert ranges[0] == range_
    print(ranges)


def run_timed_code_snippet(code: str, module_name: str) -> float:
    result = subprocess.run(
        ["python", "-c", code],
        capture_output=True,
        text=True
    )

    return float(result.stdout)


def run_code_snippet_in_subprocess(module_name: str, code_snippet: str, preload: str = "") -> float:
    code = """
import time
import {module_name}
{preload}
start = time.time()
{code_snippet}
end = time.time()
print(end - start)
    """.format(module_name=module_name, code_snippet=code_snippet, preload=preload)
    result = subprocess.run(
        ["python", "-c", code],
        capture_output=True,
        text=True
    )

    # "stdout" contains the output from the subprocess as a string
    output = result.stdout
    err = result.stderr
    return float(output)


def bench_code_snippet(module_name: str, code: str, preload: str = "", iterations: int = 1000) -> float:
    sum_time = 0.0

    for _ in range(iterations):
        sum_time += run_code_snippet_in_subprocess(module_name, code, preload)

    avg_time = sum_time / iterations
    return avg_time


def bench_import_time(module_name: str, iterations: int = 1000) -> float:
    code = """
import {}
    """.format(module_name)

    return bench_code_snippet(module_name, code, "", iterations)


def bench_object_creation(module_name: str, iterations: int = 1000) -> float:
    code = """
s = "some_string"
source = {module_name}.Source("some_source", s, {module_name}.OriginType.PARAMETER)
range_ = {module_name}.TaintRange(0, 4, source)
    """.format(module_name=module_name)

    return bench_code_snippet(module_name, code, "", iterations)


def bench_set_and_get_ranges(module_name: str, iterations: int = 1000) -> float:
    preload = """
s = "some_string"
source = {module_name}.Source("some_source", s, {module_name}.OriginType.PARAMETER)
range_ = {module_name}.TaintRange(0, 4, source)
    """.format(module_name=module_name)

    code = """
{module_name}.set_ranges(s, [range_])
ranges = {module_name}.get_ranges(s)
    """.format(module_name=module_name)

    return bench_code_snippet(module_name, code, preload, iterations)


def bench_call_lower_aspect(module_name: str, iterations: int = 1000) -> float:
    preload = """
s1 = "SomeString"
s2 = "SOMESTRING2"
s3 = "sOMeString3"
s4 = "somestring"

source = {module_name}.Source("some_source", "some_source_value", {module_name}.OriginType.PARAMETER)
range_ = {module_name}.TaintRange(0, 4, source)
{module_name}.set_ranges(s1, [range_])
{module_name}.set_ranges(s2, [range_])
{module_name}.set_ranges(s3, [range_])
{module_name}.set_ranges(s4, [range_])
    """.format(module_name=module_name)

    code = """
{module_name}.aspect_lower(None, 0, s1)
{module_name}.aspect_lower(None, 0, s2)
{module_name}.aspect_lower(None, 0, s3)
{module_name}.aspect_lower(None, 0, s4)
    """.format(module_name=module_name)

    return bench_code_snippet(module_name, code, preload, iterations)


if __name__ == "__main__":
    # print("Module import time:")
    # print("PyBind11   : %.8f" % bench_import_time("_native", 500))
    # print("Rust + Pyo3: %.8f" % bench_import_time("native_rust", 500))

    # print("Object creation:")
    # print("PyBind11   : %.8f" % bench_object_creation("_native", 500))
    # print("Rust + Pyo3: %.8f" % bench_object_creation("native_rust", 500))

    # print("Bench set and get ranges:")
    # print("PyBind11   : %.8f" % bench_set_and_get_ranges("_native", 500))
    # print("Rust + Pyo3: %.8f" % bench_set_and_get_ranges("native_rust", 500))

    print("Bench call lower aspect:")
    # print("PyBind11   : %.8f" % bench_call_lower_aspect("_native", 500))
    # print("Rust + Pyo3: %.8f" % bench_call_lower_aspect("native_rust", 500))
    print("Python: %.8f" % bench_call_lower_aspect("ddtrace.appsec._iast._taint_tracking", 1))




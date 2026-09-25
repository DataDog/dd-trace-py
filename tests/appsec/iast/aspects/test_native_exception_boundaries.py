import textwrap


def test_native_entrypoint_errors_do_not_abort(run_python_code_in_subprocess):
    code = textwrap.dedent(
        """
        from ddtrace.appsec._iast._taint_tracking._native import aspects
        from ddtrace.appsec._iast._taint_tracking._native import ops

        cases = (
            (lambda: aspects.join_aspect(b",", [1]), TypeError),
            (lambda: aspects.join_aspect(bytearray(b","), [1]), TypeError),
            (lambda: aspects.extend_aspect(), ValueError),
            (lambda: ops.new_pyobject_id(), ValueError),
        )

        for call, expected_error in cases:
            try:
                call()
            except expected_error:
                continue
            raise AssertionError(f"{call!r} did not raise {expected_error.__name__}")
        """
    )

    stdout, stderr, status, _ = run_python_code_in_subprocess(code)

    assert status == 0, (stdout, stderr)


def test_native_submodule_names():
    from ddtrace.appsec._iast._taint_tracking import _native

    assert _native.aspects.__name__ == "ddtrace.appsec._iast._taint_tracking._native.aspects"
    assert _native.ops.__name__ == "ddtrace.appsec._iast._taint_tracking._native.ops"

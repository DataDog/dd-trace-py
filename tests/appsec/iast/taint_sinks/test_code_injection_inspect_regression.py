"""
Regression tests for code injection caller-frame resolution.

These cover the two ways frame lookup can go wrong: inspect.currentframe() returning None
(preventing AttributeError when accessing f_back), and wrapt's pure-Python FunctionWrapper
adding a frame that its C extension does not.
"""

import builtins
import types
from unittest import mock

import pytest
from wrapt.wrappers import FunctionWrapper as _MaybePurePythonFunctionWrapper

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast.constants import VULN_CODE_INJECTION
from ddtrace.appsec._iast.taint_sinks.code_injection import _iast_coi
from ddtrace.appsec._iast.taint_sinks.code_injection import _resolve_caller_frame
from tests.appsec.iast.iast_utils import _get_iast_data
from tests.appsec.iast.iast_utils import _iast_patched_module


mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.code_injection")

# wrapt 1.x re-imports the C classes over the pure-Python ones defined in wrapt.wrappers, so that
# name is only the pure-Python implementation when the C extension is absent (or on wrapt 2.x).
_PURE_PYTHON_WRAPT_AVAILABLE = _MaybePurePythonFunctionWrapper.__module__.startswith("wrapt")


def _wrapt_call_template(*args, **kwargs):
    return _IAST_HOOK(_WRAPPED_EVAL, None, args, kwargs)  # noqa: F821


class TestCodeInjectionInspectSafety:
    """Test that code injection handles inspect.currentframe() edge cases."""

    def test_eval_with_currentframe_returning_none(self, iast_context_defaults):
        """
        Regression test: Verify eval handles currentframe() returning None.

        In some Python implementations (Jython, IronPython), currentframe()
        may exist but return None. This tests the None check.
        """
        code_string = '"test" + "123"'
        tainted_string = taint_pyobject(
            code_string, source_name="path", source_value=code_string, source_origin=OriginType.PATH
        )

        # Mock currentframe to return None
        with mock.patch("inspect.currentframe", return_value=None):
            # Should fall back to using provided args/kwargs or None
            result = mod.pt_eval(tainted_string)
            assert result == "test123"

            data = _get_iast_data()
            assert len(data["vulnerabilities"]) == 1
            assert data["vulnerabilities"][0]["type"] == VULN_CODE_INJECTION

    def test_eval_with_no_globals_no_locals_currentframe_none(self, iast_context_defaults):
        """
        Test eval with no globals/locals arguments when currentframe returns None.

        This is the specific case that was causing segfaults - when eval is called
        without globals/locals and currentframe returns None.
        """
        code_string = "2 + 2"
        tainted_string = taint_pyobject(
            code_string, source_name="path", source_value=code_string, source_origin=OriginType.PATH
        )

        with mock.patch("inspect.currentframe", return_value=None):
            # This should not crash
            result = eval(tainted_string)
            assert result == 4

            data = _get_iast_data()
            # Should still detect the vulnerability
            assert len(data["vulnerabilities"]) == 1

    def test_eval_with_globals_currentframe_none(self, iast_context_defaults):
        """
        Test eval with explicit globals when currentframe returns None.

        Even if currentframe fails, explicit globals should work fine.
        """
        code_string = "x * 2"
        tainted_string = taint_pyobject(
            code_string, source_name="path", source_value=code_string, source_origin=OriginType.PATH
        )

        test_globals = {"x": 10}

        with mock.patch("inspect.currentframe", return_value=None):
            result = eval(tainted_string, test_globals)
            assert result == 20

            data = _get_iast_data()
            assert len(data["vulnerabilities"]) == 1
            assert data["vulnerabilities"][0]["type"] == VULN_CODE_INJECTION

    def test_eval_with_globals_and_locals_currentframe_none(self, iast_context_defaults):
        """
        Test eval with both globals and locals when currentframe returns None.
        """
        code_string = "x + y"
        tainted_string = taint_pyobject(
            code_string, source_name="path", source_value=code_string, source_origin=OriginType.PATH
        )

        test_globals = {"x": 5}
        test_locals = {"y": 10}

        with mock.patch("inspect.currentframe", return_value=None):
            result = eval(tainted_string, test_globals, test_locals)
            assert result == 15

            data = _get_iast_data()
            assert len(data["vulnerabilities"]) == 1

    def test_eval_kwargs_globals_currentframe_none(self, iast_context_defaults):
        """
        Test eval with globals as kwarg when currentframe returns None.
        """
        code_string = "value * 3"
        tainted_string = taint_pyobject(
            code_string, source_name="path", source_value=code_string, source_origin=OriginType.PATH
        )

        with mock.patch("inspect.currentframe", return_value=None):
            result = eval(tainted_string, globals={"value": 7})
            assert result == 21

            data = _get_iast_data()
            assert len(data["vulnerabilities"]) == 1

    def test_eval_kwargs_locals_currentframe_none(self, iast_context_defaults):
        """
        Test eval with both globals and locals as kwargs when currentframe returns None.
        """
        code_string = "a + b"
        tainted_string = taint_pyobject(
            code_string, source_name="path", source_value=code_string, source_origin=OriginType.PATH
        )

        with mock.patch("inspect.currentframe", return_value=None):
            result = eval(tainted_string, {"a": 100}, locals={"b": 200})
            assert result == 300

            data = _get_iast_data()
            assert len(data["vulnerabilities"]) == 1

    def test_eval_in_function_currentframe_none(self, iast_context_defaults):
        """
        Test eval inside a function when currentframe returns None.

        When currentframe returns None, we can't extract the caller's locals,
        so the eval must be self-contained or use explicit globals/locals.
        """

        def test_function():
            code_string = "10 + 5"  # Self-contained expression
            tainted_string = taint_pyobject(
                code_string, source_name="path", source_value=code_string, source_origin=OriginType.PATH
            )

            with mock.patch("inspect.currentframe", return_value=None):
                # Without currentframe, eval with None globals/locals
                # works for self-contained expressions
                result = eval(tainted_string)
                return result

        result = test_function()
        assert result == 15

        data = _get_iast_data()
        assert len(data["vulnerabilities"]) == 1

    def test_eval_no_globals_extraction_on_currentframe_none(self, iast_context_defaults):
        """
        Verify that when currentframe returns None, we don't try to access f_back.

        This is the specific bug fix - without the None check, we'd try to access
        frames.f_back which would raise AttributeError.
        """
        code_string = "10 + 20"
        tainted_string = taint_pyobject(
            code_string, source_name="path", source_value=code_string, source_origin=OriginType.PATH
        )

        # Create a mock that explicitly returns None
        with mock.patch("inspect.currentframe", return_value=None) as mock_frame:
            result = eval(tainted_string)
            assert result == 30

            # Verify currentframe was called (attempting to get frame info)
            assert mock_frame.called

            data = _get_iast_data()
            assert len(data["vulnerabilities"]) == 1

    def test_multiple_eval_calls_with_currentframe_issues(self, iast_context_defaults):
        """
        Test multiple eval calls with various currentframe scenarios.

        This ensures the fix is stable across multiple invocations.
        """
        test_cases = [
            ("1 + 1", 2),
            ("5 * 5", 25),
            ("100 - 50", 50),
        ]

        with mock.patch("inspect.currentframe", return_value=None):
            for code_string, expected in test_cases:
                tainted_string = taint_pyobject(
                    code_string, source_name="path", source_value=code_string, source_origin=OriginType.PATH
                )
                result = eval(tainted_string)
                assert result == expected

        data = _get_iast_data()
        assert len(data["vulnerabilities"]) == len(test_cases)


class TestCodeInjectionInspectEdgeCases:
    """Additional edge case tests for inspect-related functionality."""

    def test_eval_with_frame_but_no_f_back(self, iast_context_defaults):
        """
        Test when currentframe returns a frame but it has no f_back.

        This can happen at the top level of a script.
        """
        code_string = "42"
        tainted_string = taint_pyobject(
            code_string, source_name="path", source_value=code_string, source_origin=OriginType.PATH
        )

        # Create a mock frame without f_back
        mock_frame = mock.MagicMock()
        mock_frame.f_back = None
        mock_frame.f_globals = {}
        mock_frame.f_locals = {}

        with mock.patch("inspect.currentframe", return_value=mock_frame):
            # Should handle gracefully
            try:
                result = eval(tainted_string, {"__builtins__": __builtins__})
                assert result == 42
            except AttributeError:
                pytest.fail("Should not raise AttributeError when f_back is None")

    def test_eval_lambda_with_currentframe_none(self, iast_context_defaults):
        """
        Test eval creating lambda functions when currentframe returns None.
        """
        code_string = "lambda x: x * 2"
        tainted_string = taint_pyobject(
            code_string, source_name="path", source_value=code_string, source_origin=OriginType.PATH
        )

        with mock.patch("inspect.currentframe", return_value=None):
            func = eval(tainted_string)
            assert callable(func)
            assert func(5) == 10

            data = _get_iast_data()
            assert len(data["vulnerabilities"]) == 1


class TestCodeInjectionWraptExtraFrame:
    """Verify eval resolves the application frame when a wrapt frame sits between them.

    wrapt's C extension adds no frame of its own, but the pure-Python FunctionWrapper does, so a
    fixed f_back depth lands inside wrapt and the caller's locals go missing. CI runs with the C
    extension available, so these tests recreate that frame instead of relying on
    WRAPT_DISABLE_EXTENSIONS.
    """

    @staticmethod
    def _eval_wrapped_behind_wrapt_frame():
        """Wrap eval so the hook is invoked from a frame reporting itself as wrapt.wrappers.

        This is the only thing wrapt's pure-Python FunctionWrapper contributes to the stack, and
        rebuilding it here keeps the test independent of which wrapt implementation is installed.
        """
        # Start from this module's globals so any IAST-injected names stay resolvable, then
        # override __name__ so the frame reports itself as wrapt.
        namespace = dict(globals())
        namespace.update(
            {
                "__name__": "wrapt.wrappers",
                "_IAST_HOOK": _iast_coi,
                "_WRAPPED_EVAL": getattr(builtins.eval, "__wrapped__", builtins.eval),
            }
        )
        return types.FunctionType(_wrapt_call_template.__code__, namespace, "__call__")

    def test_resolve_caller_frame_skips_wrapt_frames(self):
        """The walk must skip wrapt frames and stop at the first application frame."""
        wrapt_frame = mock.MagicMock()
        wrapt_frame.f_globals = {"__name__": "wrapt.wrappers"}
        caller_frame = mock.MagicMock()
        caller_frame.f_globals = {"__name__": "tests.appsec.iast.fixtures.taint_sinks.code_injection"}
        wrapt_frame.f_back = caller_frame

        assert _resolve_caller_frame(wrapt_frame) is caller_frame

    def test_resolve_caller_frame_returns_c_extension_frame_unchanged(self):
        """With the C extension there is no wrapt frame, so the first frame is already the caller."""
        caller_frame = mock.MagicMock()
        caller_frame.f_globals = {"__name__": "tests.appsec.iast.fixtures.taint_sinks.code_injection"}

        assert _resolve_caller_frame(caller_frame) is caller_frame

    def test_resolve_caller_frame_does_not_skip_lookalike_module(self):
        """Only the wrapt package is skipped; a module merely starting with those letters is not."""
        caller_frame = mock.MagicMock()
        caller_frame.f_globals = {"__name__": "wrapture"}

        assert _resolve_caller_frame(caller_frame) is caller_frame

    def test_resolve_caller_frame_handles_exhausted_walk(self):
        """A chain that never leaves wrapt falls back to the frame it started from."""
        frames = []
        for _ in range(12):
            frame = mock.MagicMock()
            frame.f_globals = {"__name__": "wrapt"}
            if frames:
                frames[-1].f_back = frame
            frames.append(frame)
        frames[-1].f_back = None

        assert _resolve_caller_frame(frames[0]) is frames[0]

    def test_eval_resolves_caller_locals(self, iast_context_defaults):
        """Regression: the evaluated expression reads a local of the calling application frame."""
        code_string = "local_value + 1"
        tainted_string = taint_pyobject(
            code_string, source_name="path", source_value=code_string, source_origin=OriginType.PATH
        )

        with mock.patch.object(builtins, "eval", self._eval_wrapped_behind_wrapt_frame()):
            result = mod.pt_eval_caller_locals(tainted_string)

        assert result == 43

        data = _get_iast_data()
        assert len(data["vulnerabilities"]) == 1
        assert data["vulnerabilities"][0]["type"] == VULN_CODE_INJECTION

    def test_eval_with_explicit_globals_is_unaffected(self, iast_context_defaults):
        """Explicit globals bypass frame resolution entirely and must keep working."""
        code_string = "x * 2"
        tainted_string = taint_pyobject(
            code_string, source_name="path", source_value=code_string, source_origin=OriginType.PATH
        )

        with mock.patch.object(builtins, "eval", self._eval_wrapped_behind_wrapt_frame()):
            result = mod.pt_eval_globals(tainted_string)

        assert result == 10

        data = _get_iast_data()
        assert len(data["vulnerabilities"]) == 1
        assert data["vulnerabilities"][0]["type"] == VULN_CODE_INJECTION

    @pytest.mark.skipif(not _PURE_PYTHON_WRAPT_AVAILABLE, reason="wrapt.wrappers exposes the C FunctionWrapper here")
    def test_eval_resolves_caller_locals_with_real_pure_python_wrapper(self, iast_context_defaults):
        """Same regression driven through wrapt's own pure-Python wrapper, to catch frame shape drift."""
        code_string = "local_value + 1"
        tainted_string = taint_pyobject(
            code_string, source_name="path", source_value=code_string, source_origin=OriginType.PATH
        )

        original_eval = getattr(builtins.eval, "__wrapped__", builtins.eval)
        wrapped_eval = _MaybePurePythonFunctionWrapper(original_eval, _iast_coi)
        with mock.patch.object(builtins, "eval", wrapped_eval):
            result = mod.pt_eval_caller_locals(tainted_string)

        assert result == 43

        data = _get_iast_data()
        assert len(data["vulnerabilities"]) == 1
        assert data["vulnerabilities"][0]["type"] == VULN_CODE_INJECTION


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

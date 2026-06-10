"""Integration tests for the test discovery mode (DD_TEST_OPTIMIZATION_DISCOVERY_ENABLED)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from _pytest.pytester import Pytester
import pytest


class TestDiscoveryMode:
    """Tests for pytest_collection_finish in discovery mode.

    Discovery mode: DD_TEST_OPTIMIZATION_DISCOVERY_ENABLED=1 causes pytest to write
    collected tests as JSON Lines to a file and exit without running any tests.
    """

    @pytest.fixture(autouse=True)
    def output_file(self, pytester: Pytester, tmp_path: Path) -> Path:
        """Use a stable output path inside the pytester tmpdir for each test."""
        output = pytester.path / "discovery_output.json"
        pytester.monkeypatch.setenv("DD_TEST_OPTIMIZATION_DISCOVERY_ENABLED", "1")
        pytester.monkeypatch.setenv("DD_TEST_OPTIMIZATION_DISCOVERY_FILE", str(output))
        return output

    def _read_discovered(self, output_file: Path) -> list[dict]:
        return [json.loads(line) for line in output_file.read_text().splitlines() if line.strip()]

    # ------------------------------------------------------------------
    # Basic behaviour
    # ------------------------------------------------------------------

    def test_writes_json_lines_file(self, pytester: Pytester, output_file: Path) -> None:
        """Discovery mode writes one JSON object per line for each collected test."""
        pytester.makepyfile(
            test_foo="""
            def test_alpha():
                pass

            def test_beta():
                pass
            """
        )

        result = pytester.inline_run()

        assert result.ret == 0
        tests = self._read_discovered(output_file)
        assert len(tests) == 2
        names = {t["name"] for t in tests}
        assert names == {"test_alpha", "test_beta"}

    def test_does_not_run_tests(self, pytester: Pytester, output_file: Path) -> None:
        """Discovery mode exits after collection — test bodies are never executed."""
        pytester.makepyfile(
            test_boom="""
            def test_would_fail():
                raise RuntimeError("This should never run")
            """
        )

        result = pytester.inline_run()

        assert result.ret == 0

    def test_json_fields_are_correct(self, pytester: Pytester, output_file: Path) -> None:
        """Each JSON object has name, suite, module, parameters, suiteSourceFile."""
        pytester.makepyfile(
            test_fields="""
            def test_something():
                pass
            """
        )

        pytester.inline_run()

        (entry,) = self._read_discovered(output_file)
        assert entry["name"] == "test_something"
        assert entry["suite"] == "test_fields.py"
        assert entry["module"] == "pytest"
        assert entry["parameters"] is None
        assert entry["suiteSourceFile"].endswith("test_fields.py")

    def test_exit_code_zero(self, pytester: Pytester, output_file: Path) -> None:
        """Discovery mode always exits with code 0."""
        pytester.makepyfile(test_x="def test_pass(): pass")
        result = pytester.inline_run()
        assert result.ret == 0

    # ------------------------------------------------------------------
    # Parametrized tests
    # ------------------------------------------------------------------

    def test_parametrized_tests_include_parameters(self, pytester: Pytester, output_file: Path) -> None:
        """Parametrized tests have their parameters encoded in the parameters field."""
        pytester.makepyfile(
            test_params="""
            import pytest

            @pytest.mark.parametrize("x,y", [(1, 2), (3, 4)])
            def test_add(x, y):
                pass
            """
        )

        pytester.inline_run()

        tests = self._read_discovered(output_file)
        assert len(tests) == 2
        for entry in tests:
            assert entry["parameters"] is not None
            params = json.loads(entry["parameters"])
            assert "arguments" in params
            assert "x" in params["arguments"]
            assert "y" in params["arguments"]

    # ------------------------------------------------------------------
    # Skip filtering
    # ------------------------------------------------------------------

    def test_skip_marked_tests_are_excluded(self, pytester: Pytester, output_file: Path) -> None:
        """Tests decorated with @pytest.mark.skip are excluded from the output."""
        pytester.makepyfile(
            test_skip="""
            import pytest

            @pytest.mark.skip(reason="not ready")
            def test_skipped():
                pass

            def test_included():
                pass
            """
        )

        pytester.inline_run()

        tests = self._read_discovered(output_file)
        assert len(tests) == 1
        assert tests[0]["name"] == "test_included"

    def test_skipif_true_tests_are_excluded(self, pytester: Pytester, output_file: Path) -> None:
        """Tests with @pytest.mark.skipif(True, ...) are excluded from the output."""
        pytester.makepyfile(
            test_skipif="""
            import pytest

            @pytest.mark.skipif(True, reason="always skip")
            def test_always_skipped():
                pass

            @pytest.mark.skipif(False, reason="never skip")
            def test_never_skipped():
                pass
            """
        )

        pytester.inline_run()

        tests = self._read_discovered(output_file)
        assert len(tests) == 1
        assert tests[0]["name"] == "test_never_skipped"

    def test_skipif_with_version_check(self, pytester: Pytester, output_file: Path) -> None:
        """Tests with @pytest.mark.skipif(sys.version_info...) are handled correctly."""
        always_true = sys.version_info >= (2, 0)
        always_false = sys.version_info >= (99, 0)

        pytester.makepyfile(
            test_version=f"""
            import sys
            import pytest

            @pytest.mark.skipif(sys.version_info >= (2, 0), reason="skipped on all Pythons")
            def test_skipped_on_all():
                pass

            @pytest.mark.skipif(sys.version_info >= (99, 0), reason="only on Python 99+")
            def test_included_on_all():
                pass
            """
        )

        pytester.inline_run()

        tests = self._read_discovered(output_file)
        names = {t["name"] for t in tests}

        if always_true:
            assert "test_skipped_on_all" not in names
        if not always_false:
            assert "test_included_on_all" in names

    def test_skipif_string_condition_included(self, pytester: Pytester, output_file: Path) -> None:
        """Tests with string-form skipif conditions are conservatively included."""
        pytester.makepyfile(
            test_string_skip="""
            import pytest

            @pytest.mark.skipif("True", reason="string condition not evaluated")
            def test_with_string_condition():
                pass
            """
        )

        pytester.inline_run()

        tests = self._read_discovered(output_file)
        assert len(tests) == 1
        assert tests[0]["name"] == "test_with_string_condition"

    # ------------------------------------------------------------------
    # Interaction with --ddtrace
    # ------------------------------------------------------------------

    def test_discovery_mode_disables_ci_visibility(self, pytester: Pytester, output_file: Path) -> None:
        """When discovery mode is active, CI visibility is not initialised even if --ddtrace is passed."""
        pytester.makepyfile(test_ci="def test_one(): pass")

        # Should exit cleanly with no API key / agent configured
        result = pytester.inline_run("--ddtrace")
        assert result.ret == 0
        tests = self._read_discovered(output_file)
        assert len(tests) == 1

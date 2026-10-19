"""Tests for shared coverage utilities."""

from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

import pytest


class TestCoverageUtils:
    """Test suite for coverage utility functions."""

    def test_handle_coverage_report_successful_upload(self):
        """Test successful coverage report generation and upload."""
        from ddtrace.contrib.internal.coverage.utils import handle_coverage_report

        mock_session = Mock()
        mock_upload_func = Mock(return_value=True)
        mock_is_pytest_cov_enabled = Mock(return_value=False)
        lifecycle_calls = []
        mock_stop_coverage = Mock(side_effect=lambda **kwargs: lifecycle_calls.append("stop"))

        test_lcov_content = b"SF:test_file.py\nDA:1,1\nDA:2,1\nend_of_record\n"

        with (
            patch("ddtrace.contrib.internal.coverage.utils.is_coverage_running", return_value=True),
            patch(
                "ddtrace.contrib.internal.coverage.utils._generate_lcov_report",
                side_effect=lambda *args, **kwargs: lifecycle_calls.append("report") or 85.5,
            ) as mock_generate,
            patch("pathlib.Path.read_bytes", return_value=test_lcov_content),
            patch("pathlib.Path.unlink") as mock_unlink,
            patch("ddtrace.contrib.internal.coverage.utils.log") as mock_log,
        ):
            handle_coverage_report(mock_session, mock_upload_func, mock_is_pytest_cov_enabled, mock_stop_coverage)

            # Verify LCOV report was generated
            mock_generate.assert_called_once()

            # Verify upload was attempted
            mock_upload_func.assert_called_once_with(test_lcov_content, "lcov")

            # Verify cleanup occurred
            mock_unlink.assert_called_once()

            # Coverage should be stopped since pytest-cov is disabled
            mock_stop_coverage.assert_called_once_with(save=True)
            assert lifecycle_calls == ["stop", "report"]

            # Verify success log
            mock_log.debug.assert_called_with("Successfully uploaded coverage report")

    def test_handle_coverage_report_upload_failure(self):
        """Test handling of upload failure."""
        from ddtrace.contrib.internal.coverage.utils import handle_coverage_report

        mock_session = Mock()
        mock_upload_func = Mock(return_value=False)  # Upload fails
        mock_is_pytest_cov_enabled = Mock(return_value=False)
        mock_stop_coverage = Mock()

        test_lcov_content = b"SF:test_file.py\nDA:1,1\nend_of_record\n"

        with (
            patch("ddtrace.contrib.internal.coverage.utils.is_coverage_running", return_value=True),
            patch("ddtrace.contrib.internal.coverage.utils.generate_lcov_report", return_value=75.0),
            patch("pathlib.Path.read_bytes", return_value=test_lcov_content),
            patch("pathlib.Path.unlink"),
            patch("ddtrace.contrib.internal.coverage.utils.log") as mock_log,
        ):
            handle_coverage_report(mock_session, mock_upload_func, mock_is_pytest_cov_enabled, mock_stop_coverage)

            # Upload should be attempted
            mock_upload_func.assert_called_once()

            # Should log warning on failure
            mock_log.warning.assert_called_with("Failed to upload coverage report")

            # Coverage should still be stopped
            mock_stop_coverage.assert_called_once_with(save=True)

    def test_handle_coverage_report_no_coverage_running(self):
        """Test when coverage is not running."""
        from ddtrace.contrib.internal.coverage.utils import handle_coverage_report

        mock_session = Mock()
        mock_upload_func = Mock()
        mock_is_pytest_cov_enabled = Mock(return_value=False)
        mock_stop_coverage = Mock()

        with (
            patch("ddtrace.contrib.internal.coverage.utils.is_coverage_running", return_value=False),
            patch("ddtrace.contrib.internal.coverage.utils.log") as mock_log,
        ):
            handle_coverage_report(mock_session, mock_upload_func, mock_is_pytest_cov_enabled, mock_stop_coverage)

            # Nothing should be called
            mock_upload_func.assert_not_called()
            mock_stop_coverage.assert_not_called()

            # Should log debug message
            mock_log.debug.assert_called_with("Coverage is not running, skipping coverage report upload")

    def test_handle_coverage_report_with_pytest_cov(self):
        """Test coverage upload when pytest-cov is enabled."""
        from ddtrace.contrib.internal.coverage.utils import handle_coverage_report

        mock_session = Mock()
        mock_upload_func = Mock(return_value=True)
        mock_is_pytest_cov_enabled = Mock(return_value=True)  # pytest-cov enabled
        mock_stop_coverage = Mock()

        test_lcov_content = b"SF:test_file.py\nDA:1,1\nend_of_record\n"

        with (
            patch("ddtrace.contrib.internal.coverage.utils.is_coverage_running", return_value=True),
            patch("ddtrace.contrib.internal.coverage.utils.generate_lcov_report", return_value=90.0),
            patch("pathlib.Path.read_bytes", return_value=test_lcov_content),
            patch("pathlib.Path.unlink"),
        ):
            handle_coverage_report(mock_session, mock_upload_func, mock_is_pytest_cov_enabled, mock_stop_coverage)

            # Upload should happen
            mock_upload_func.assert_called_once_with(test_lcov_content, "lcov")

            # Coverage should NOT be stopped (pytest-cov manages it)
            mock_stop_coverage.assert_not_called()

    def test_handle_coverage_report_pytest_cov_registration(self):
        """Test registration of pytest-cov coverage instance."""
        from ddtrace.contrib.internal.coverage.utils import handle_coverage_report

        mock_session = Mock()
        mock_upload_func = Mock(return_value=True)
        mock_is_pytest_cov_enabled = Mock(return_value=True)

        # Mock pytest-cov plugin
        mock_cov_controller = Mock()
        mock_cov_instance = Mock()
        mock_cov_controller.cov = mock_cov_instance

        mock_plugin = Mock()
        mock_plugin.cov_controller = mock_cov_controller

        # Mock session.config.pluginmanager.list_name_plugin()
        mock_session.config.pluginmanager.list_name_plugin.return_value = [
            ("other_plugin", Mock()),
            ("pytest_cov_plugin", mock_plugin),
        ]

        test_lcov_content = b"SF:test_file.py\nend_of_record\n"

        with (
            patch("ddtrace.contrib.internal.coverage.utils.is_coverage_running", side_effect=[False, True]),
            patch("ddtrace.contrib.internal.coverage.utils.set_coverage_instance") as mock_set_coverage,
            patch("ddtrace.contrib.internal.coverage.utils.generate_lcov_report", return_value=85.0),
            patch("pathlib.Path.read_bytes", return_value=test_lcov_content),
            patch("pathlib.Path.unlink"),
            patch("ddtrace.contrib.internal.coverage.utils.log") as mock_log,
        ):
            handle_coverage_report(
                mock_session,
                mock_upload_func,
                mock_is_pytest_cov_enabled,
            )

            # Should register pytest-cov coverage instance
            mock_set_coverage.assert_called_once_with(mock_cov_instance)
            mock_log.debug.assert_any_call("Registered pytest-cov coverage instance with ddtrace")

            # Upload should happen after registration
            mock_upload_func.assert_called_once()

    def test_handle_coverage_report_exception_during_generation(self):
        """Test exception handling during coverage report generation."""
        from ddtrace.contrib.internal.coverage.utils import handle_coverage_report

        mock_session = Mock()
        mock_upload_func = Mock()
        mock_is_pytest_cov_enabled = Mock(return_value=False)
        mock_stop_coverage = Mock()

        with (
            patch("ddtrace.contrib.internal.coverage.utils.is_coverage_running", return_value=True),
            patch(
                "ddtrace.contrib.internal.coverage.utils.generate_lcov_report",
                side_effect=RuntimeError("Coverage generation failed"),
            ),
            patch("ddtrace.contrib.internal.coverage.utils.log") as mock_log,
        ):
            # Should not raise exception
            handle_coverage_report(mock_session, mock_upload_func, mock_is_pytest_cov_enabled, mock_stop_coverage)

            # Upload should not be called
            mock_upload_func.assert_not_called()

            # Should log exception
            mock_log.exception.assert_called()

            # Should still try to stop coverage
            mock_stop_coverage.assert_called_once_with(save=True)

    def test_handle_coverage_report_exception_during_upload(self):
        """Test exception handling during upload."""
        from ddtrace.contrib.internal.coverage.utils import handle_coverage_report

        mock_session = Mock()
        mock_upload_func = Mock(side_effect=RuntimeError("Upload failed"))
        mock_is_pytest_cov_enabled = Mock(return_value=False)
        mock_stop_coverage = Mock()

        test_lcov_content = b"SF:test_file.py\nend_of_record\n"

        with (
            patch("ddtrace.contrib.internal.coverage.utils.is_coverage_running", return_value=True),
            patch("ddtrace.contrib.internal.coverage.utils.generate_lcov_report", return_value=80.0),
            patch("pathlib.Path.read_bytes", return_value=test_lcov_content),
            patch("pathlib.Path.unlink"),
            patch("ddtrace.contrib.internal.coverage.utils.log") as mock_log,
        ):
            # Should not raise exception
            handle_coverage_report(mock_session, mock_upload_func, mock_is_pytest_cov_enabled, mock_stop_coverage)

            # Should log exception
            mock_log.exception.assert_called()

            # Should still stop coverage
            mock_stop_coverage.assert_called_once_with(save=True)

    def test_handle_coverage_report_cleanup_failure(self):
        """Test graceful handling of cleanup failure."""
        from ddtrace.contrib.internal.coverage.utils import handle_coverage_report

        mock_session = Mock()
        mock_upload_func = Mock(return_value=True)
        mock_is_pytest_cov_enabled = Mock(return_value=False)
        mock_stop_coverage = Mock()

        test_lcov_content = b"SF:test_file.py\nend_of_record\n"

        with (
            patch("ddtrace.contrib.internal.coverage.utils.is_coverage_running", return_value=True),
            patch("ddtrace.contrib.internal.coverage.utils.generate_lcov_report", return_value=85.0),
            patch("pathlib.Path.read_bytes", return_value=test_lcov_content),
            patch("pathlib.Path.unlink", side_effect=OSError("Permission denied")),
            patch("ddtrace.contrib.internal.coverage.utils.log") as mock_log,
        ):
            # Should not raise exception
            handle_coverage_report(mock_session, mock_upload_func, mock_is_pytest_cov_enabled, mock_stop_coverage)

            # Upload should still happen
            mock_upload_func.assert_called_once()

            # Should log cleanup failure
            mock_log.debug.assert_any_call(
                "Failed to clean up temporary coverage report file: %s", mock_log.debug.call_args_list[-1][0][1]
            )

    def test_handle_coverage_report_no_stop_coverage_function(self):
        """Test when no stop_coverage function is provided."""
        from ddtrace.contrib.internal.coverage.utils import handle_coverage_report

        mock_session = Mock()
        mock_upload_func = Mock(return_value=True)
        mock_is_pytest_cov_enabled = Mock(return_value=False)

        test_lcov_content = b"SF:test_file.py\nend_of_record\n"

        with (
            patch("ddtrace.contrib.internal.coverage.utils.is_coverage_running", return_value=True),
            patch("ddtrace.contrib.internal.coverage.utils.generate_lcov_report", return_value=85.0),
            patch("pathlib.Path.read_bytes", return_value=test_lcov_content),
            patch("pathlib.Path.unlink"),
        ):
            # Call without stop_coverage function
            handle_coverage_report(
                mock_session,
                mock_upload_func,
                mock_is_pytest_cov_enabled,
            )

            # Should still work and upload
            mock_upload_func.assert_called_once()

    def test_handle_coverage_report_exception_in_stop_coverage(self):
        """Test graceful handling when stop_coverage fails."""
        from ddtrace.contrib.internal.coverage.utils import handle_coverage_report

        mock_session = Mock()
        mock_upload_func = Mock(return_value=True)
        mock_is_pytest_cov_enabled = Mock(return_value=False)
        mock_stop_coverage = Mock(side_effect=RuntimeError("Stop failed"))

        test_lcov_content = b"SF:test_file.py\nend_of_record\n"

        with (
            patch("ddtrace.contrib.internal.coverage.utils.is_coverage_running", return_value=True),
            patch("ddtrace.contrib.internal.coverage.utils.generate_lcov_report", return_value=85.0),
            patch("pathlib.Path.read_bytes", return_value=test_lcov_content),
            patch("pathlib.Path.unlink"),
            patch("ddtrace.contrib.internal.coverage.utils.log"),
        ):
            # Should not raise exception
            handle_coverage_report(mock_session, mock_upload_func, mock_is_pytest_cov_enabled, mock_stop_coverage)

            # Upload should still happen
            mock_upload_func.assert_called_once()

    def test_handle_coverage_report_top_level_exception(self):
        """Test top-level exception handling."""
        from ddtrace.contrib.internal.coverage.utils import handle_coverage_report

        mock_session = Mock()
        mock_upload_func = Mock()
        mock_is_pytest_cov_enabled = Mock(side_effect=RuntimeError("Unexpected error"))

        with (
            patch("ddtrace.contrib.internal.coverage.utils.log") as mock_log,
        ):
            # Should not raise exception
            handle_coverage_report(
                mock_session,
                mock_upload_func,
                mock_is_pytest_cov_enabled,
            )

            # Should log top-level exception
            mock_log.exception.assert_called_with(
                "Error in coverage report upload handling: %s", mock_log.exception.call_args[0][1]
            )


class TestCoverageUtilityFunctions:
    """Test individual utility functions."""

    def test_is_coverage_loaded(self):
        """Test is_coverage_loaded function."""
        from ddtrace.contrib.internal.coverage.utils import is_coverage_loaded

        # Test when coverage is not loaded
        with patch.dict("sys.modules", {}, clear=False):
            if "coverage" in __import__("sys").modules:
                del __import__("sys").modules["coverage"]
            assert is_coverage_loaded() is False

        # Test when coverage is loaded
        with patch.dict("sys.modules", {"coverage": Mock()}, clear=False):
            assert is_coverage_loaded() is True

    def test_is_coverage_patched(self):
        """Test _is_coverage_patched function."""
        from ddtrace.contrib.internal.coverage.utils import _is_coverage_patched

        # Test when coverage is not loaded
        with patch("ddtrace.contrib.internal.coverage.utils.is_coverage_loaded", return_value=False):
            assert _is_coverage_patched() is False

        # Test when coverage is loaded but not patched
        mock_coverage = Mock()
        del mock_coverage._datadog_patch  # Ensure attribute doesn't exist
        with (
            patch("ddtrace.contrib.internal.coverage.utils.is_coverage_loaded", return_value=True),
            patch.dict("sys.modules", {"coverage": mock_coverage}),
        ):
            assert _is_coverage_patched() is False

        # Test when coverage is loaded and patched
        mock_coverage._datadog_patch = True
        with (
            patch("ddtrace.contrib.internal.coverage.utils.is_coverage_loaded", return_value=True),
            patch.dict("sys.modules", {"coverage": mock_coverage}),
        ):
            assert _is_coverage_patched() is True

    def test_command_invokes_coverage_run(self):
        """Test _command_invokes_coverage_run function."""
        from ddtrace.contrib.internal.coverage.utils import _command_invokes_coverage_run

        # Test positive cases
        assert _command_invokes_coverage_run(["coverage", "run", "-m", "pytest"]) is True
        assert _command_invokes_coverage_run(["python", "-m", "coverage", "run", "-m", "pytest"]) is True

        # Test negative cases
        assert _command_invokes_coverage_run(["pytest"]) is False
        assert _command_invokes_coverage_run(["python", "-m", "pytest"]) is False
        assert _command_invokes_coverage_run(["coverage", "report"]) is False

    def test_is_coverage_invoked_by_coverage_run(self):
        """Test _is_coverage_invoked_by_coverage_run function."""
        from ddtrace.contrib.internal.coverage.utils import _is_coverage_invoked_by_coverage_run

        # Test with environment variable
        with patch("ddtrace.contrib.internal.coverage.utils._get_config", return_value=True):
            assert _is_coverage_invoked_by_coverage_run() is True

        # Test with command line detection
        with (
            patch("ddtrace.contrib.internal.coverage.utils._get_config", return_value=False),
            patch("ddtrace.contrib.internal.coverage.utils._command_invokes_coverage_run", return_value=True),
        ):
            assert _is_coverage_invoked_by_coverage_run() is True

        # Test negative case
        with (
            patch("ddtrace.contrib.internal.coverage.utils._get_config", return_value=False),
            patch("ddtrace.contrib.internal.coverage.utils._command_invokes_coverage_run", return_value=False),
        ):
            assert _is_coverage_invoked_by_coverage_run() is False


class TestRemapLcovPaths:
    """Tests for _remap_lcov_paths and _build_path_aliases."""

    @pytest.mark.parametrize("checkout_exists", [True, False])
    def test_remap_wheel_installed_paths(self, tmp_path, monkeypatch, checkout_exists):
        """SF: lines with site-packages paths are remapped to repo paths."""
        from ddtrace.contrib.internal.coverage.utils import _remap_lcov_paths

        config = Path(__file__).resolve().parents[4] / ".coveragerc"
        (tmp_path / ".coveragerc").write_text(config.read_text(), encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        if checkout_exists:
            (tmp_path / "ddtrace/internal/coverage").mkdir(parents=True)
            (tmp_path / "ddtrace/__init__.py").touch()
            (tmp_path / "ddtrace/internal/coverage/instrumentation_py3_10.py").touch()

        lcov = (
            "TN:ddtrace\n"
            "SF:.cache/uv-test-environments/100b0a9-df168796e73c/lib/python3.12/site-packages/ddtrace/__init__.py\n"
            "DA:1,1\n"
            "end_of_record\n"
            "SF:.cache/uv-test-environments/10210f3-2925f25ab8c0/lib/python3.9/site-packages/ddtrace/__init__.py\n"
            "DA:1,1\n"
            "end_of_record\n"
            "SF:.cache/uv-test-environments/102b11d-879899dc816e/lib/python3.11/site-packages/"
            "ddtrace/internal/coverage/instrumentation_py3_10.py\n"
            "DA:1,0\n"
            "end_of_record\n"
        )
        lcov_file = tmp_path / "test.lcov"
        lcov_file.write_text(lcov, encoding="utf-8")

        _remap_lcov_paths(lcov_file)

        result = lcov_file.read_text(encoding="utf-8")
        import re

        sf_lines = re.findall(r"^SF:(.+)$", result, re.MULTILINE)
        assert len(sf_lines) == 3  # still 3 records
        assert set(sf_lines) == {
            "ddtrace/__init__.py",
            "ddtrace/internal/coverage/instrumentation_py3_10.py",
        }

    @pytest.mark.parametrize("pytest_cov_enabled", [True, False])
    @pytest.mark.parametrize("relative_files", [True, False])
    def test_upload_remaps_wheel_paths_without_checkout(
        self, tmp_path, monkeypatch, pytest_cov_enabled, relative_files
    ):
        """The uploaded report uses aliases even when only installed sources exist."""
        from coverage import Coverage

        from ddtrace.contrib.internal.coverage.utils import handle_coverage_report

        monkeypatch.chdir(tmp_path)
        # Use a custom config to also verify that the reporting instance owns the aliases.
        cov_config = tmp_path / "custom.coveragerc"
        cov_config.write_text(
            f"[run]\nrelative_files = {relative_files}\n[paths]\npackage =\n    src/package/\n"
            "    */site-packages/package/\n",
            encoding="utf-8",
        )
        cov = Coverage(config_file=str(cov_config), data_file=None)
        aliases = {}
        for env, version in (("100b0a9", "3.12"), ("10210f3", "3.9"), ("1027a5e", "3.9"), ("102b11d", "3.11")):
            for filename in ("__init__.py", "internal/coverage.py"):
                installed = Path(
                    f".cache/uv-test-environments/{env}/lib/python{version}/site-packages/package/{filename}"
                )
                installed.parent.mkdir(parents=True, exist_ok=True)
                installed.write_text("value = 1\n", encoding="utf-8")
                measured = installed if relative_files else installed.resolve()
                cov.get_data().add_lines({str(measured): {1}})
                aliases[installed.as_posix()] = f"src/package/{filename}"
        assert not (tmp_path / "src").exists()

        original_report = tmp_path / "original.lcov"
        cov.lcov_report(outfile=str(original_report))
        expected = original_report.read_bytes()
        for installed, canonical in aliases.items():
            source_line = f"SF:{installed}\n".encode()
            assert source_line in expected
            expected = expected.replace(source_line, f"SF:{canonical}\n".encode())

        upload = Mock(return_value=True)
        with (
            patch("ddtrace.contrib.internal.coverage.utils.is_coverage_running", return_value=True),
            patch("ddtrace.contrib.internal.coverage.utils.get_coverage_instance", return_value=cov),
            patch("ddtrace.contrib.internal.coverage.patch.get_coverage_instance", return_value=cov),
            patch("ddtrace.contrib.internal.coverage.utils._find_pytest_cov_instance", return_value=cov),
        ):
            handle_coverage_report(Mock(), upload, lambda config: pytest_cov_enabled)

        upload.assert_called_once_with(expected, "lcov")
        assert {line for line in expected.splitlines() if line.startswith(b"SF:")} == {
            b"SF:src/package/__init__.py",
            b"SF:src/package/internal/coverage.py",
        }

    def test_remap_noop_for_repo_paths(self, tmp_path):
        """SF: lines that are already repo-relative are left unchanged."""
        from ddtrace.contrib.internal.coverage.utils import _remap_lcov_paths

        lcov = "SF:ddtrace/__init__.py\nDA:1,1\nend_of_record\n"
        lcov_file = tmp_path / "test.lcov"
        lcov_file.write_text(lcov, encoding="utf-8")

        _remap_lcov_paths(lcov_file)

        assert lcov_file.read_text(encoding="utf-8") == lcov

    def test_remap_noop_for_non_ddtrace_paths(self, tmp_path):
        """SF: lines for test files (not under site-packages/ddtrace) are left unchanged."""
        from ddtrace.contrib.internal.coverage.utils import _remap_lcov_paths

        lcov = "SF:tests/test_foo.py\nDA:1,1\nend_of_record\n"
        lcov_file = tmp_path / "test.lcov"
        lcov_file.write_text(lcov, encoding="utf-8")

        _remap_lcov_paths(lcov_file)

        assert lcov_file.read_text(encoding="utf-8") == lcov

    def test_remap_noop_when_no_paths_configured(self, tmp_path):
        """_remap_lcov_paths is a no-op when [paths] has no aliases."""
        from ddtrace.contrib.internal.coverage.utils import _remap_lcov_paths

        lcov = "SF:foo/bar.py\nDA:1,1\nend_of_record\n"
        lcov_file = tmp_path / "test.lcov"
        lcov_file.write_text(lcov, encoding="utf-8")

        with patch("ddtrace.contrib.internal.coverage.utils._build_path_aliases", return_value=None):
            _remap_lcov_paths(lcov_file)

        assert lcov_file.read_text(encoding="utf-8") == lcov

    def test_remap_empty_file(self, tmp_path):
        """An empty LCOV file is handled gracefully."""
        from ddtrace.contrib.internal.coverage.utils import _remap_lcov_paths

        lcov_file = tmp_path / "empty.lcov"
        lcov_file.write_text("", encoding="utf-8")

        _remap_lcov_paths(lcov_file)

        assert lcov_file.read_text(encoding="utf-8") == ""

    def test_remap_preserves_non_sf_lines(self, tmp_path):
        """DA, FN, end_of_record, and other lines are preserved unchanged."""
        from ddtrace.contrib.internal.coverage.utils import _remap_lcov_paths

        lcov = (
            "TN:ddtrace\n"
            "SF:.cache/uv-test-environments/abc/lib/python3.12/site-packages/ddtrace/__init__.py\n"
            "FN:1,func\n"
            "FNDA:1,func\n"
            "DA:1,1\n"
            "DA:2,0\n"
            "BRDA:1,0,0,1\n"
            "end_of_record\n"
        )
        lcov_file = tmp_path / "test.lcov"
        lcov_file.write_text(lcov, encoding="utf-8")

        _remap_lcov_paths(lcov_file)

        result = lcov_file.read_text(encoding="utf-8")
        # SF line should be remapped
        assert "SF:ddtrace/__init__.py\n" in result
        # All other lines preserved
        assert "TN:ddtrace\n" in result
        assert "FN:1,func\n" in result
        assert "FNDA:1,func\n" in result
        assert "DA:1,1\n" in result
        assert "DA:2,0\n" in result
        assert "BRDA:1,0,0,1\n" in result
        assert "end_of_record\n" in result

    def test_build_path_aliases_returns_none_when_no_coverage(self):
        """_build_path_aliases returns None when coverage.py is not installed."""
        with patch("ddtrace.contrib.internal.coverage.utils._Coverage", None):
            from ddtrace.contrib.internal.coverage.utils import _build_path_aliases

            assert _build_path_aliases() is None

    def test_build_path_aliases_uses_active_coverage_instance(self):
        """_build_path_aliases uses the provided coverage instance instead of creating a new one."""
        mock_cov = Mock()
        mock_aliases = Mock()
        mock_aliases.aliases = [("pattern", None, "result")]
        mock_cov._make_aliases.return_value = mock_aliases

        from ddtrace.contrib.internal.coverage.utils import _build_path_aliases

        result = _build_path_aliases(cov_instance=mock_cov)

        assert result is mock_aliases
        mock_cov._make_aliases.assert_called_once()

    def test_remap_uses_active_coverage_config(self, tmp_path):
        """_remap_lcov_paths uses aliases from the provided coverage instance, not the default config."""
        import re

        from ddtrace.contrib.internal.coverage.utils import _remap_lcov_paths

        # Build a mock aliases object with a real regex that maps
        # some/installed/ to custom/ (mimicking a [paths] entry).
        mock_aliases = Mock()
        mock_aliases.aliases = [("pattern", re.compile(r"some/installed/"), "custom/")]

        mock_cov = Mock()
        mock_cov._make_aliases.return_value = mock_aliases

        lcov = "SF:some/installed/path.py\nDA:1,1\nend_of_record\n"
        lcov_file = tmp_path / "test.lcov"
        lcov_file.write_text(lcov, encoding="utf-8")

        with patch("ddtrace.contrib.internal.coverage.utils.Path.cwd", return_value=Path("/repo")):
            _remap_lcov_paths(lcov_file, cov_instance=mock_cov)

        result = lcov_file.read_text(encoding="utf-8")
        assert "SF:custom/path.py\n" in result
        mock_cov._make_aliases.assert_called_once()

    def test_remap_not_poisoned_by_canonical_filename_cache(self, tmp_path, monkeypatch):
        """Remapping must not be affected by coverage.py's global canonical_filename cache.

        coverage.py's ``PathAliases.map`` calls ``canonical_filename`` on the
        *mapped* path, which searches ``cwd`` and ``sys.path`` and caches the
        result globally.  When the checkout sources have been removed, a stale
        cache entry for the mapped filename can resolve it back to an
        installed-package location, defeating the alias.

        This test seeds the cache for the exact mapped filename
        (``mylib/__init__.py``) with an incorrect installed-package path, then
        verifies that ``_remap_lcov_paths`` still produces the correct relative
        path because it applies aliases directly without canonicalisation.
        """
        import re

        from coverage.files import CANONICAL_FILENAME_CACHE

        from ddtrace.contrib.internal.coverage.utils import _remap_lcov_paths

        monkeypatch.chdir(tmp_path)

        # Build a real Coverage instance with a [paths] entry that maps
        # */site-packages/mylib/ to mylib/
        cov_config = tmp_path / ".coveragerc"
        cov_config.write_text(
            "[run]\n[paths]\nmylib =\n    mylib/\n    */site-packages/mylib/\n",
            encoding="utf-8",
        )
        from coverage import Coverage

        cov = Coverage(config_file=str(cov_config), data_file=None)

        # Poison the canonical_filename cache for the exact mapped filename.
        # The alias maps */site-packages/mylib/__init__.py → mylib/__init__.py.
        # PathAliases.map would then call canonical_filename("mylib/__init__.py"),
        # which searches sys.path and could resolve it to an installed copy.
        # We simulate that by pre-seeding the cache with a bogus absolute path.
        poisoned_path = str(tmp_path / "site-packages" / "mylib" / "__init__.py")
        CANONICAL_FILENAME_CACHE["mylib/__init__.py"] = poisoned_path

        lcov = (
            "SF:.cache/uv/env-a/lib/python3.12/site-packages/mylib/__init__.py\n"
            "DA:1,1\n"
            "end_of_record\n"
            "SF:.cache/uv/env-b/lib/python3.9/site-packages/mylib/__init__.py\n"
            "DA:1,1\n"
            "end_of_record\n"
        )
        lcov_file = tmp_path / "test.lcov"
        lcov_file.write_text(lcov, encoding="utf-8")

        _remap_lcov_paths(lcov_file, cov_instance=cov)

        result = lcov_file.read_text(encoding="utf-8")
        sf_lines = re.findall(r"^SF:(.+)$", result, re.MULTILINE)
        assert len(sf_lines) == 2
        assert set(sf_lines) == {"mylib/__init__.py"}

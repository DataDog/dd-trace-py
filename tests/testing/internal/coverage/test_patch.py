"""Tests for ddtrace.contrib.internal.coverage.patch module."""

from inspect import signature
from io import StringIO
from pathlib import Path
import re
import runpy
import tempfile
from unittest.mock import Mock
from unittest.mock import patch

from coverage import Coverage
from coverage.exceptions import NoDataError
import pytest

from ddtrace.contrib.internal.coverage import patch as coverage_patch


@pytest.fixture
def measured_coverage(tmp_path: Path) -> tuple[Coverage, Path]:
    """Return isolated coverage data for a fully executed source file."""
    source_path = tmp_path / "measured.py"
    source_path.write_text("value = 1\nvalue += 1\n")

    cov = Coverage(config_file=False, data_file=None, include=[str(source_path)])
    cov.start()
    try:
        runpy.run_path(str(source_path))
    finally:
        cov.stop()

    return cov, source_path


class TestCoverageIntegration:
    """Tests for coverage.py integration functions."""

    def test_start_and_stop_coverage(self) -> None:
        """Test starting and stopping coverage collection."""
        # Start coverage
        coverage_patch.start_coverage()
        assert coverage_patch.is_coverage_running()

        # Get coverage instance
        cov = coverage_patch.get_coverage_instance()
        assert cov is not None

        # Stop coverage
        coverage_patch.stop_coverage(save=False, erase=True)
        assert not coverage_patch.is_coverage_running()

    def test_stop_coverage_does_not_modify_external_instance(self) -> None:
        """Coverage sessions started by tools such as pytest-cov remain externally managed."""
        external_cov = Mock()

        with patch.object(coverage_patch.Coverage, "current", return_value=external_cov):
            assert coverage_patch.start_coverage() is external_cov

        assert coverage_patch.stop_coverage(save=True, erase=True) is external_cov
        external_cov.stop.assert_not_called()
        external_cov.save.assert_not_called()
        external_cov.erase.assert_not_called()
        coverage_patch.reset_coverage_state()

    def test_generate_lcov_report_returns_percentage(
        self, measured_coverage: tuple[Coverage, Path], tmp_path: Path
    ) -> None:
        """Test that generating LCOV report returns coverage percentage."""
        cov, source_path = measured_coverage
        report_path = tmp_path / "coverage.lcov"

        pct_covered = coverage_patch.generate_lcov_report(cov=cov, outfile=str(report_path))

        assert pct_covered == 100.0
        assert report_path.exists()
        lcov_content = report_path.read_text()
        assert f"SF:{source_path}" in lcov_content
        assert "DA:1,1" in lcov_content
        assert "DA:2,1" in lcov_content
        assert "end_of_record" in lcov_content

    def test_get_coverage_percentage(self, measured_coverage: tuple[Coverage, Path], tmp_path: Path) -> None:
        """Test retrieving stored coverage percentage."""
        cov, _ = measured_coverage
        report_path = tmp_path / "coverage.lcov"

        pct_from_gen = coverage_patch.generate_lcov_report(cov=cov, outfile=str(report_path))

        assert pct_from_gen == 100.0
        assert coverage_patch.get_coverage_percentage() == pct_from_gen

    def test_coverage_instance_available_when_running(self) -> None:
        """Test that coverage instance is available when coverage is running."""
        coverage_patch.start_coverage()

        cov = coverage_patch.get_coverage_instance()
        assert cov is not None

        # Should be able to call coverage methods
        assert hasattr(cov, "start")
        assert hasattr(cov, "stop")
        assert hasattr(cov, "save")

        coverage_patch.stop_coverage(save=False, erase=True)

    def test_coverage_instance_none_when_not_running(self) -> None:
        """Test that coverage instance is None when not running."""
        # Ensure coverage is not running
        if coverage_patch.is_coverage_running():
            coverage_patch.stop_coverage(save=False, erase=True)

        cov = coverage_patch.get_coverage_instance()
        assert cov is None

    def test_generate_report_without_coverage_running(self) -> None:
        """Test generating report when coverage was not running."""
        # Ensure coverage is stopped
        if coverage_patch.is_coverage_running():
            coverage_patch.stop_coverage(save=False, erase=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "coverage.lcov"

            # Should handle gracefully
            pct = coverage_patch.generate_lcov_report(outfile=str(report_path))

            # May return None or 0 depending on implementation
            assert pct is None or pct == 0.0

    def test_multiple_start_stop_cycles(self) -> None:
        """Test multiple cycles of starting and stopping coverage."""
        # First cycle
        coverage_patch.start_coverage()
        assert coverage_patch.is_coverage_running()
        coverage_patch.stop_coverage(save=False, erase=True)
        assert not coverage_patch.is_coverage_running()

        # Second cycle
        coverage_patch.start_coverage()
        assert coverage_patch.is_coverage_running()
        coverage_patch.stop_coverage(save=False, erase=True)
        assert not coverage_patch.is_coverage_running()

    def test_stop_coverage_saves_data(self) -> None:
        """Test that stopping coverage with save=True preserves data."""
        coverage_patch.start_coverage()

        # Execute some code
        def test_func():
            return 42

        test_func()

        # Stop with save
        coverage_patch.stop_coverage(save=True, erase=False)

        # Get coverage data should not be empty
        data = coverage_patch.get_coverage_data()
        assert data is not None

        # Cleanup
        coverage_patch.erase_coverage()

    def test_lcov_report_with_no_data(self, tmp_path: Path) -> None:
        """Test generating LCOV report with no coverage data."""
        cov = coverage_patch.start_coverage(config_file=False, data_file=None, omit=["*"])
        assert cov is not None
        coverage_patch.stop_coverage(save=False)
        report_path = tmp_path / "coverage.lcov"

        with pytest.raises(NoDataError):
            cov.lcov_report(outfile=str(report_path))

        assert coverage_patch.generate_lcov_report(cov=cov, outfile=str(report_path)) is None

    def test_get_coverage_data_returns_dict(self) -> None:
        """Test that get_coverage_data returns a dictionary."""
        coverage_patch.start_coverage()

        # Execute some code
        _ = 1 + 1

        coverage_patch.stop_coverage(save=True)

        data = coverage_patch.get_coverage_data()
        assert data is not None
        assert isinstance(data, dict)

        coverage_patch.erase_coverage()


class TestCoverageErrorHandling:
    """Tests for error handling in coverage integration."""

    def test_stop_coverage_when_not_started(self) -> None:
        """Test stopping coverage when it was never started."""
        # Ensure coverage is not running
        if coverage_patch.is_coverage_running():
            coverage_patch.stop_coverage(save=False, erase=True)

        # Should handle gracefully without raising
        coverage_patch.stop_coverage()
        assert not coverage_patch.is_coverage_running()

    def test_generate_report_with_invalid_path(self) -> None:
        """Test that an output-path error is handled without masking the failure."""
        invalid_path = "/nonexistent/directory/coverage.lcov"
        error = OSError("unable to write coverage report")
        cov = Mock()
        cov.lcov_report.side_effect = error

        with patch.object(coverage_patch.log, "warning") as log_warning:
            result = coverage_patch.generate_lcov_report(cov=cov, outfile=invalid_path)

        assert result is None
        cov.lcov_report.assert_called_once_with(outfile=invalid_path)
        log_warning.assert_called_once_with("An exception occurred when running a coverage report: %s", error)

    def test_erase_coverage_when_not_running(self) -> None:
        """Test erasing coverage data when coverage is not running."""
        # Ensure coverage is not running
        if coverage_patch.is_coverage_running():
            coverage_patch.stop_coverage(save=False, erase=True)

        # Should handle gracefully
        coverage_patch.erase_coverage()
        assert not coverage_patch.is_coverage_running()


class TestCoveragePatching:
    """Tests for coverage patching functionality."""

    def test_patch_and_unpatch_coverage(self) -> None:
        """Test patching and unpatching coverage.py."""
        # Ensure coverage is not patched initially
        coverage_patch.unpatch()

        # Patch coverage
        coverage_patch.patch()

        # Should be marked as patched
        assert hasattr(coverage_patch.coverage, "_datadog_patch")
        assert coverage_patch.coverage._datadog_patch is True

        # Unpatch coverage
        coverage_patch.unpatch()

        # Should no longer be patched
        assert not hasattr(coverage_patch.coverage, "_datadog_patch") or coverage_patch.coverage._datadog_patch is False

    def test_double_patch_is_safe(self) -> None:
        """Test that patching twice doesn't cause issues."""
        coverage_patch.unpatch()

        # Patch twice
        coverage_patch.patch()
        coverage_patch.patch()

        # Should still be marked as patched once
        assert coverage_patch.coverage._datadog_patch is True  # type:ignore[attr-defined]

        coverage_patch.unpatch()

    def test_double_unpatch_is_safe(self) -> None:
        """Test that unpatching twice doesn't cause issues."""
        coverage_patch.patch()

        # Unpatch twice
        coverage_patch.unpatch()
        coverage_patch.unpatch()

        # Should not cause errors
        assert not hasattr(coverage_patch.coverage, "_datadog_patch") or coverage_patch.coverage._datadog_patch is False

    def test_coverage_report_wrapper_caches_percentage(self) -> None:
        """Test that the coverage report wrapper caches the percentage."""
        coverage_patch.reset_coverage_state()

        # Mock function that returns a percentage
        def mock_report_func(*args, **kwargs):
            return 85.5

        # Call the wrapper
        result = coverage_patch.coverage_report_wrapper(mock_report_func, None, (), {})

        # Should return the percentage and cache it
        assert result == 85.5
        assert coverage_patch.get_coverage_percentage() == 85.5

    def test_generate_coverage_report_with_different_formats(
        self, measured_coverage: tuple[Coverage, Path], tmp_path: Path
    ) -> None:
        """Test generating coverage reports with different formats."""
        cov, source_path = measured_coverage
        text_output = StringIO()
        lcov_path = tmp_path / "coverage.lcov"

        text_pct = coverage_patch.generate_coverage_report("text", cov=cov, file=text_output)
        lcov_pct = coverage_patch.generate_coverage_report("lcov", cov=cov, outfile=str(lcov_path))

        assert text_pct == 100.0
        assert lcov_pct == 100.0
        assert "100%" in text_output.getvalue()
        lcov_content = lcov_path.read_text()
        assert f"SF:{source_path}" in lcov_content
        assert "end_of_record" in lcov_content
        assert coverage_patch.get_coverage_percentage() == lcov_pct

    def test_start_coverage_with_custom_parameters(self) -> None:
        """Test starting coverage with custom parameters."""
        if coverage_patch.is_coverage_running():
            coverage_patch.stop_coverage(erase=True)

        # Start with custom parameters
        cov = coverage_patch.start_coverage(source=["test_file.py"], omit=["*/tests/*"], auto_data=True)

        assert cov is not None
        assert coverage_patch.is_coverage_running()

        # Stop and cleanup
        coverage_patch.stop_coverage(save=False, erase=True)

    def test_coverage_instance_management(self) -> None:
        """Test coverage instance management functions."""
        # Start with clean state
        coverage_patch.reset_coverage_state()

        # Should be None initially
        assert coverage_patch.get_coverage_instance() is None

        # Start coverage
        cov = coverage_patch.start_coverage()
        assert cov is not None

        # Should be able to get the same instance
        same_cov = coverage_patch.get_coverage_instance()
        assert same_cov is not None

        coverage_patch.stop_coverage(save=False, erase=True)

        # Set a different instance
        mock_cov = Mock()
        coverage_patch.set_coverage_instance(mock_cov)
        retrieved_cov = coverage_patch.get_coverage_instance()
        assert retrieved_cov is mock_cov

        # Reset state
        # Note: Coverage.current() might still return an instance even after reset
        coverage_patch.reset_coverage_state()

    def test_get_coverage_data_backwards_compatibility(self) -> None:
        """Test get_coverage_data function for backwards compatibility."""
        coverage_patch.reset_coverage_state()

        # Should return empty dict when no percentage cached
        data = coverage_patch.get_coverage_data()
        assert data == {}

        # Set a percentage and verify it's returned
        coverage_patch.start_coverage()
        coverage_patch.stop_coverage()

        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "coverage.lcov"
            pct = coverage_patch.generate_lcov_report(outfile=str(report_path))

            if pct is not None:
                data = coverage_patch.get_coverage_data()
                assert coverage_patch.PCT_COVERED_KEY in data
                assert data[coverage_patch.PCT_COVERED_KEY] == pct

        coverage_patch.erase_coverage()


class TestLcovReportMemory:
    @pytest.mark.parametrize("branch", [False, True])
    @pytest.mark.parametrize("filtered", [False, True])
    def test_lcov_matches_coverage_report(self, tmp_path: Path, branch: bool, filtered: bool) -> None:
        cov = Coverage(config_file=False, data_file=None, source=[str(tmp_path)], branch=branch)
        sources = [tmp_path / name for name in ("z_module.py", "a_é_module.py", "omit_module.py")]
        for path in sources:
            path.write_text(
                "def choose(value):\n    if value:\n        return 'yes'\n    return 'no'\nchoose(True)\n",
                encoding="utf-8",
            )
        cov.start()
        cov.switch_context("selected")
        for path in sources:
            runpy.run_path(str(path))
        cov.switch_context("other")
        runpy.run_path(str(sources[0]))
        cov.stop()
        cov.set_option("report:skip_empty", True)
        options = {"contexts": ["selected"], "ignore_errors": True}
        if filtered:
            options.update({"include": [str(tmp_path / "*_module.py")], "omit": [str(sources[-1])]})
        original = tmp_path / "original.lcov"
        generated = tmp_path / "generated.lcov"
        expected_percentage = cov.lcov_report(outfile=str(original), **options)
        expected_config = cov.config

        percentage = coverage_patch.generate_lcov_report(cov=cov, outfile=str(generated), **options)

        assert generated.read_bytes() == original.read_bytes()
        assert percentage == expected_percentage
        assert cov.config is expected_config

    @pytest.mark.parametrize("legacy_shape", [None, "reporters", "pairs"])
    def test_lcov_releases_completed_file_analyses(self, tmp_path: Path, monkeypatch, legacy_shape) -> None:
        import gc
        import weakref

        from coverage.lcovreport import LcovReporter
        from coverage.plugin import FileReporter

        cov = Coverage(config_file=False, data_file=None, source=[str(tmp_path)])
        for i in range(12):
            path = tmp_path / f"module_{i:02}.py"
            path.write_text("value = 1\n")
        cov.start()
        for path in sorted(tmp_path.glob("*.py")):
            runpy.run_path(str(path))
        cov.stop()
        if legacy_shape:
            original_analyze = Coverage._analyze
            original_get_reporters = Coverage._get_file_reporters
            has_reporter_argument = "file_reporter" in signature(original_analyze).parameters

            def legacy_analyze(self, morf):
                if isinstance(morf, FileReporter):
                    if has_reporter_argument:
                        return original_analyze(self, morf.filename, file_reporter=morf)
                    # Intermediate APIs only accept filenames, including as
                    # hashable cache keys. Translate the simulated old API.
                    morf = morf.filename
                return original_analyze(self, morf)

            for name in ("cache_clear", "cache_info"):
                if hasattr(original_analyze, name):
                    setattr(legacy_analyze, name, getattr(original_analyze, name))

            def legacy_get_reporters(self, morfs):
                entries = original_get_reporters(self, morfs)
                pairs = [entry if isinstance(entry, tuple) else (entry, entry.filename) for entry in entries]
                if legacy_shape == "reporters":
                    return [fr for fr, _ in pairs]
                return pairs

            monkeypatch.setattr(Coverage, "_analyze", legacy_analyze)
            monkeypatch.setattr(Coverage, "_get_file_reporters", legacy_get_reporters)
        renderer_name = "lcov_file" if hasattr(LcovReporter, "lcov_file") else "get_lcov"
        original_render = getattr(LcovReporter, renderer_name)
        reporters = []
        live_counts = []

        def observe_render(self, *args):
            gc.collect()
            file_reporter = args[1] if renderer_name == "lcov_file" else args[0]
            reporters.append(weakref.ref(file_reporter))
            live_counts.append(sum(ref() is not None for ref in reporters))
            return original_render(self, *args)

        monkeypatch.setattr(LcovReporter, renderer_name, observe_render)

        percentage = coverage_patch.generate_lcov_report(cov=cov, outfile=str(tmp_path / "report.lcov"))

        assert percentage == 100.0
        assert len(reporters) == 12
        assert max(live_counts) == 1
        for method in (cov._analyze, cov._get_file_reporter):
            if hasattr(method, "cache_info"):
                assert method.cache_info().currsize == 0

    @pytest.mark.parametrize("ignore_errors", [False, True])
    @pytest.mark.parametrize("invalid_source", ["syntax", "missing", "non_python"])
    def test_lcov_preserves_analysis_errors(self, tmp_path: Path, monkeypatch, ignore_errors, invalid_source) -> None:
        from ddtrace.contrib.internal.coverage import lcov

        source = tmp_path / "valid.py"
        source.write_text("value = 1\n")
        invalid = tmp_path / ("invalid.txt" if invalid_source == "non_python" else "invalid.py")
        if invalid_source != "missing":
            invalid.write_text("this is invalid Python!\n")
        cov = Coverage(config_file=False, data_file=None)
        cov.get_data().add_lines({str(source): {1}, str(invalid): {1}})
        warnings = []
        monkeypatch.setattr(cov, "_warn", lambda message, **kwargs: warnings.append((message, kwargs)))
        native_path = tmp_path / "native.lcov"
        generated_path = tmp_path / "generated.lcov"

        try:
            expected = cov.lcov_report(outfile=str(native_path), ignore_errors=ignore_errors)
        except Exception as exc:
            expected_warnings = warnings.copy()
            warnings.clear()
            with pytest.raises(type(exc), match=re.escape(str(exc))):
                lcov.report_lcov(cov, outfile=str(generated_path), ignore_errors=ignore_errors)
            assert not generated_path.exists()
        else:
            expected_warnings = warnings.copy()
            warnings.clear()
            actual = lcov.report_lcov(cov, outfile=str(generated_path), ignore_errors=ignore_errors)
            assert actual == expected
            assert generated_path.read_bytes() == native_path.read_bytes()

        assert warnings == expected_warnings

    @pytest.mark.parametrize("filtered", [False, True])
    def test_lcov_preserves_no_data_error(self, tmp_path: Path, filtered) -> None:
        from ddtrace.contrib.internal.coverage import lcov

        cov = Coverage(config_file=False, data_file=None)
        options = {}
        if filtered:
            source = tmp_path / "module.py"
            source.write_text("value = 1\n")
            cov.get_data().add_lines({str(source): {1}})
            options["omit"] = [str(source)]
        report = tmp_path / "report.lcov"

        with pytest.raises(NoDataError, match="No data to report"):
            lcov.report_lcov(cov, outfile=str(report), **options)

        assert not report.exists()

    def test_lcov_copies_large_unicode_records(self, tmp_path: Path) -> None:
        path = tmp_path / "large_é_module.py"
        path.write_text("value = 'é'\n" * 9000, encoding="utf-8")
        cov = Coverage(config_file=False, data_file=None)
        cov.get_data().add_lines({str(path): set(range(1, 9001, 2))})
        original = tmp_path / "original.lcov"
        generated = tmp_path / "generated.lcov"
        expected_percentage = cov.lcov_report(outfile=str(original))

        percentage = coverage_patch.generate_lcov_report(cov=cov, outfile=str(generated))

        assert original.stat().st_size > 65536
        assert generated.read_bytes() == original.read_bytes()
        assert percentage == expected_percentage

    def test_lcov_closes_spool_when_rendering_fails(self, tmp_path: Path, monkeypatch) -> None:
        from coverage.lcovreport import LcovReporter

        from ddtrace.contrib.internal.coverage import lcov

        path = tmp_path / "module.py"
        path.write_text("value = 1\n")
        cov = Coverage(config_file=False, data_file=None)
        cov.get_data().add_lines({str(path): {1}})
        temporary_file = tempfile.TemporaryFile
        spools = []

        def track_spool(*args, **kwargs):
            spool = temporary_file(*args, **kwargs)
            spools.append(spool)
            return spool

        def fail_render(*args, **kwargs):
            raise RuntimeError("render failed")

        monkeypatch.setattr(lcov.tempfile, "TemporaryFile", track_spool)
        renderer_name = "lcov_file" if hasattr(LcovReporter, "lcov_file") else "get_lcov"
        monkeypatch.setattr(LcovReporter, renderer_name, fail_render)

        with pytest.raises(RuntimeError, match="render failed"):
            lcov.report_lcov(cov, outfile=str(tmp_path / "report.lcov"))

        assert len(spools) == 1
        assert spools[0].closed

    @pytest.mark.parametrize("cached_analysis", [False, True])
    def test_lcov_applies_and_clears_context_filters(self, tmp_path: Path, monkeypatch, cached_analysis) -> None:
        if cached_analysis:
            import functools

            original_analyze = getattr(Coverage._analyze, "__wrapped__", Coverage._analyze)
            original_get_reporters = Coverage._get_file_reporters

            @functools.lru_cache(maxsize=1)
            def analyze(self, morf):
                return original_analyze(self, morf)

            def get_reporters(self, morfs):
                entries = original_get_reporters(self, morfs)
                return [entry if isinstance(entry, tuple) else (entry, entry.filename) for entry in entries]

            monkeypatch.setattr(Coverage, "_analyze", analyze)
            monkeypatch.setattr(Coverage, "_get_file_reporters", get_reporters)

        path = tmp_path / "contexts.py"
        path.write_text("first = 1\nsecond = 2\n")
        cov = Coverage(config_file=False, data_file=None)
        data = cov.get_data()
        data.set_context("selected")
        data.add_lines({str(path): {1}})
        data.set_context("other")
        data.add_lines({str(path): {2}})
        report = tmp_path / "contexts.lcov"

        cov.set_option("report:contexts", ["selected"])
        percentage = coverage_patch.generate_lcov_report(cov=cov, outfile=str(report))
        assert percentage == 50.0
        assert "DA:1,1" in report.read_text()
        assert "DA:2,0" in report.read_text()

        percentage = coverage_patch.generate_lcov_report(cov=cov, outfile=str(report), contexts=["other"])
        assert percentage == 50.0
        assert "DA:1,0" in report.read_text()
        assert "DA:2,1" in report.read_text()
        assert cov.get_option("report:contexts") == ["selected"]

        cov.set_option("report:contexts", None)
        percentage = coverage_patch.generate_lcov_report(cov=cov, outfile=str(report))
        assert percentage == 100.0
        assert "DA:1,1" in report.read_text()
        assert "DA:2,1" in report.read_text()

    def test_lcov_uses_native_reporter_without_file_renderer(self, tmp_path: Path, monkeypatch) -> None:
        from coverage.lcovreport import LcovReporter

        from ddtrace.contrib.internal.coverage import lcov

        cov = Coverage(config_file=False, data_file=None)
        monkeypatch.delattr(LcovReporter, "lcov_file", raising=False)
        monkeypatch.delattr(LcovReporter, "get_lcov", raising=False)
        options = {"outfile": str(tmp_path / "report.lcov"), "contexts": ["selected"], "ignore_errors": True}
        with (
            patch.object(cov, "lcov_report", return_value=50.0) as native_report,
            patch.object(lcov.tempfile, "TemporaryFile", side_effect=AssertionError("No spool expected")),
        ):
            percentage = lcov.report_lcov(cov, **options)

        assert percentage == 50.0
        native_report.assert_called_once_with(**options)

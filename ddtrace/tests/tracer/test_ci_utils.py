"""Tests for GitHub Actions CI provider (job ID resolution)."""

import json
import os
import tempfile
from unittest import mock

import pytest

from ddtrace.ext.ci import github_actions


class TestGitHubActionsJobID:
    """Test GitHub Actions job ID resolution from environment and diagnostics files."""

    def test_get_github_actions_job_id_from_env_var(self):
        """Test job ID extraction from JOB_CHECK_RUN_ID environment variable."""
        env = {"JOB_CHECK_RUN_ID": "12345678"}
        job_id = github_actions._get_job_id(env)
        assert job_id == "12345678"

    def test_get_github_actions_job_id_from_env_var_with_whitespace(self):
        """Test job ID extraction with whitespace trimming."""
        env = {"JOB_CHECK_RUN_ID": "  12345678  "}
        job_id = github_actions._get_job_id(env)
        assert job_id == "12345678"

    def test_get_github_actions_job_id_rejects_non_numeric_env_var(self):
        """Test that non-numeric environment variable is rejected."""
        env = {"JOB_CHECK_RUN_ID": "abc123"}
        with mock.patch.object(github_actions, "_get_diag_dirs", return_value=[]):
            job_id = github_actions._get_job_id(env)
            assert job_id is None

    def test_get_github_actions_job_id_from_diag_json(self):
        """Test job ID extraction from diagnostics file with JSON format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a Worker log file with JSON format
            worker_file = os.path.join(tmpdir, "Worker_20240101-000000.log")
            diag_data = {
                "job": {
                    "d": [
                        {"k": "check_run_id", "v": 87654321},
                        {"k": "other_key", "v": "other_value"},
                    ]
                }
            }
            with open(worker_file, "w") as f:
                json.dump(diag_data, f)

            # Mock the diagnostics directories to use our temp dir
            with mock.patch.object(github_actions, "_get_diag_dirs", return_value=[tmpdir]):
                env = {}
                job_id = github_actions._get_job_id(env)
                assert job_id == "87654321"

    def test_get_github_actions_job_id_from_diag_json_float(self):
        """Test job ID extraction from diagnostics file with float value."""
        with tempfile.TemporaryDirectory() as tmpdir:
            worker_file = os.path.join(tmpdir, "Worker_20240101-000000.log")
            diag_data = {
                "job": {
                    "d": [
                        {"k": "check_run_id", "v": 87654321.0},
                    ]
                }
            }
            with open(worker_file, "w") as f:
                json.dump(diag_data, f)

            with mock.patch.object(github_actions, "_get_diag_dirs", return_value=[tmpdir]):
                env = {}
                job_id = github_actions._get_job_id(env)
                assert job_id == "87654321"

    def test_get_github_actions_job_id_from_diag_json_string(self):
        """Test job ID extraction from diagnostics file with string value."""
        with tempfile.TemporaryDirectory() as tmpdir:
            worker_file = os.path.join(tmpdir, "Worker_20240101-000000.log")
            diag_data = {
                "job": {
                    "d": [
                        {"k": "check_run_id", "v": "87654321"},
                    ]
                }
            }
            with open(worker_file, "w") as f:
                json.dump(diag_data, f)

            with mock.patch.object(github_actions, "_get_diag_dirs", return_value=[tmpdir]):
                env = {}
                job_id = github_actions._get_job_id(env)
                assert job_id == "87654321"

    def test_get_github_actions_job_id_from_diag_regex(self):
        """Test job ID extraction from diagnostics file using regex fallback."""
        with tempfile.TemporaryDirectory() as tmpdir:
            worker_file = os.path.join(tmpdir, "Worker_20240101-000000.log")
            # Write non-JSON content that matches the regex pattern
            content = 'some log content "k": "check_run_id", "v": 99887766 more content'
            with open(worker_file, "w") as f:
                f.write(content)

            with mock.patch.object(github_actions, "_get_diag_dirs", return_value=[tmpdir]):
                env = {}
                job_id = github_actions._get_job_id(env)
                assert job_id == "99887766"

    def test_get_github_actions_job_id_multiple_files_newest_first(self):
        """Test that the newest diagnostics file is checked first."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create older file with one job ID
            older_file = os.path.join(tmpdir, "Worker_20240101-000000.log")
            diag_data_old = {
                "job": {
                    "d": [
                        {"k": "check_run_id", "v": 11111111},
                    ]
                }
            }
            with open(older_file, "w") as f:
                json.dump(diag_data_old, f)

            # Create newer file with different job ID
            import time

            time.sleep(0.01)  # Ensure different mtime
            newer_file = os.path.join(tmpdir, "Worker_20240101-120000.log")
            diag_data_new = {
                "job": {
                    "d": [
                        {"k": "check_run_id", "v": 22222222},
                    ]
                }
            }
            with open(newer_file, "w") as f:
                json.dump(diag_data_new, f)

            with mock.patch.object(github_actions, "_get_diag_dirs", return_value=[tmpdir]):
                env = {}
                job_id = github_actions._get_job_id(env)
                assert job_id == "22222222"  # Should get the newer file's ID

    def test_get_github_actions_job_id_env_var_priority(self):
        """Test that environment variable takes priority over diagnostics file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            worker_file = os.path.join(tmpdir, "Worker_20240101-000000.log")
            diag_data = {
                "job": {
                    "d": [
                        {"k": "check_run_id", "v": 87654321},
                    ]
                }
            }
            with open(worker_file, "w") as f:
                json.dump(diag_data, f)

            with mock.patch.object(github_actions, "_get_diag_dirs", return_value=[tmpdir]):
                env = {"JOB_CHECK_RUN_ID": "12345678"}
                job_id = github_actions._get_job_id(env)
                assert job_id == "12345678"  # Env var wins

    def test_get_github_actions_job_id_no_worker_files(self):
        """Test that None is returned when no Worker files exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(github_actions, "_get_diag_dirs", return_value=[tmpdir]):
                env = {}
                job_id = github_actions._get_job_id(env)
                assert job_id is None

    def test_get_github_actions_job_id_nonexistent_directory(self):
        """Test that None is returned when diagnostics directory doesn't exist."""
        with mock.patch.object(github_actions, "_get_diag_dirs", return_value=["/nonexistent/path"]):
            env = {}
            job_id = github_actions._get_job_id(env)
            assert job_id is None

    def test_get_github_actions_job_id_oversized_file(self):
        """Test that oversized files are skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            worker_file = os.path.join(tmpdir, "Worker_20240101-000000.log")
            # Create a file larger than the max size
            with open(worker_file, "w") as f:
                f.write("x" * (github_actions.MAX_DIAG_FILE_SIZE + 1))

            with mock.patch.object(github_actions, "_get_diag_dirs", return_value=[tmpdir]):
                env = {}
                job_id = github_actions._get_job_id(env)
                assert job_id is None

    @pytest.mark.parametrize(
        "system,expected_dirs",
        [
            ("Linux", github_actions.DIAG_DIRS_LINUX),
            ("Darwin", github_actions.DIAG_DIRS_DARWIN),
        ],
    )
    def test_get_github_diag_dirs_by_platform(self, system, expected_dirs):
        """Test that correct diagnostics directories are returned for each platform."""
        with mock.patch("platform.system", return_value=system):
            dirs = github_actions._get_diag_dirs()
            assert dirs == expected_dirs

    def test_get_github_diag_dirs_windows(self):
        """Test Windows diagnostics directory paths."""
        with mock.patch("platform.system", return_value="Windows"):
            with mock.patch.dict(os.environ, {"ProgramFiles": "C:\\Program Files"}):
                dirs = github_actions._get_diag_dirs()
                assert os.path.join("C:\\Program Files", "actions-runner", "cached", "_diag") in dirs
                assert os.path.join("C:\\Program Files", "actions-runner", "_diag") in dirs
                assert r"C:\actions-runner\cached\_diag" in dirs
                assert r"C:\actions-runner\_diag" in dirs

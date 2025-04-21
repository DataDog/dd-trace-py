import json
import os
import subprocess
import sys
import tempfile
import time
from typing import Union


class IntegrationUpdateOrchestrator:
    TOOLING_VENV_DIR = ".venv-registry-tools"
    TOOLING_DEPS = ["pyyaml", "riot", "filelock"]
    REGISTRY_UPDATER_MODULE = "tests.contrib.integration_registry.registry_update_helpers.integration_registry_updater"
    REGISTRY_UPDATER_CLASS = "IntegrationRegistryUpdater"
    MAIN_UPDATE_SCRIPT = "scripts/integration_registry/update_and_format_registry.py"
    UPDATER_LOCK_FILE = "ddtrace/contrib/integration_registry/registry.yaml.lock"
    LOCK_MAX_WAIT_SECONDS = 15

    def __init__(self, project_root: str):
        self.project_root = project_root
        self.tooling_env_path = os.path.join(project_root, self.TOOLING_VENV_DIR)
        # Define path for the venv setup lock relative to project root
        self.venv_lock_file_path = os.path.join(project_root, ".venv-registry-tools.lock")
        self.updater_lock_file_path = os.path.join(project_root, self.UPDATER_LOCK_FILE)

    def _acquire_lock(self, lock_file_path):
        start_time = time.monotonic()
        while time.monotonic() - start_time < self.LOCK_MAX_WAIT_SECONDS:
            try:
                fd = os.open(lock_file_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return True
            except FileExistsError:
                time.sleep(0.5)
            except Exception:
                return False
        return False

    def _release_lock(self, lock_file_path):
        try:
            os.remove(lock_file_path)
        except Exception:
            pass

    def _ensure_tooling_venv(self):
        """Ensures the integration registry tools venv is created and up to date."""
        tooling_python = os.path.join(self.tooling_env_path, "bin", "python")
        pip_timeout = 20

        if os.path.exists(tooling_python):
            try:
                cmd = [tooling_python, "-m", "pip", "install", "-U"] + self.TOOLING_DEPS
                if self._run_subprocess(cmd, pip_timeout, self.project_root, "pip install -U", verbose=False):
                    return True
                else:
                    return True
            except Exception:
                return True

        try:
            cmd = ["python3", "-m", "venv", self.tooling_env_path]
            if not self._run_subprocess(cmd, 20, self.project_root, "venv creation", verbose=False):
                return False
        except Exception:
            return False

        if not os.path.exists(tooling_python):
            return False
        try:
            cmd = [tooling_python, "-m", "pip", "install"] + self.TOOLING_DEPS
            if not self._run_subprocess(cmd, pip_timeout, self.project_root, "pip install", verbose=False):
                return False
            return True
        except Exception:
            return False

    def _run_subprocess(self, cmd: list, timeout: int, cwd: str, description: str, verbose: bool = True) -> bool:
        """Helper to run subprocess. Prints stderr on failure by default."""
        try:
            process = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout, cwd=cwd)
            if verbose:
                if process.stdout:
                    print(f"\n--- stdout: {description} ---\n{process.stdout.strip()}", file=sys.stdout)
                if process.stderr:
                    print(f"\n--- stderr: {description} ---\n{process.stderr.strip()}", file=sys.stdout)
            return True
        except subprocess.CalledProcessError as e:
            print(f"Error: {description} failed (code {e.returncode}).", file=sys.stderr)
            if e.stderr:
                print(f"Stderr:\n{e.stderr}", file=sys.stderr)
            return False
        except Exception:
            return False

    @staticmethod
    def export_registry_data(data: dict, request) -> Union[str, None]:
        """Exports registry data to a temporary file for pytest worker use."""
        data_file_path = None
        try:
            unique_id = f"pid{os.getpid()}"
            worker_input = getattr(request.config, "workerinput", None)
            if worker_input and "workerid" in worker_input:
                unique_id += f"_{worker_input['workerid']}"
            temp_dir = getattr(request.config, "_tmp_path_factory", None)
            base_dir = temp_dir.getbasetemp() if temp_dir else None
            fd, data_file_path = tempfile.mkstemp(
                prefix=f"registry_data_{unique_id}_", suffix=".json", dir=base_dir, text=True
            )
            with open(fd, "w", encoding="utf-8") as temp_f:
                json.dump(data, temp_f)
            request.config._registry_session_data_file = data_file_path
            return data_file_path
        except Exception:
            if data_file_path and os.path.exists(data_file_path):
                try:
                    os.remove(data_file_path)
                except OSError:
                    pass
            if hasattr(request.config, "_registry_session_data_file"):
                delattr(request.config, "_registry_session_data_file")
            return None

    @staticmethod
    def cleanup_session_data(session):
        data_file_path = getattr(session.config, "_registry_session_data_file", None)
        if data_file_path and os.path.exists(data_file_path):
            try:
                os.remove(data_file_path)
            except OSError:
                pass
        if hasattr(session.config, "_registry_session_data_file"):
            delattr(session.config, "_registry_session_data_file")

    def run(self, data_file_path: str):
        """Main method for orchestrating the integrationregistry update process."""
        venv_lock_acquired = False
        updater_succeeded = False

        try:
            # Setup Tooling Venv
            try:
                if not self._acquire_lock(self.venv_lock_file_path):
                    return
                venv_lock_acquired = True
                if not self._ensure_tooling_venv():
                    return
            finally:
                if venv_lock_acquired:
                    self._release_lock(self.venv_lock_file_path)

            # Remove potentially stale updater lock file
            if os.path.exists(self.updater_lock_file_path):
                try:
                    os.remove(self.updater_lock_file_path)
                except OSError:
                    pass

            # Run Update Process
            tooling_python = os.path.join(self.tooling_env_path, "bin", "python")
            if not os.path.exists(tooling_python):
                return

            # 1. Run IntegrationRegistryUpdater
            escaped_path = data_file_path.replace("'", "'\\''")
            py_cmd = (
                f"import sys; sys.path.insert(0, '{self.project_root}'); "
                f"from {self.REGISTRY_UPDATER_MODULE} import {self.REGISTRY_UPDATER_CLASS}; "
                f"updater = {self.REGISTRY_UPDATER_CLASS}(); success = updater.run('{escaped_path}'); "
                f"sys.exit(0 if success else 1);"
            )
            cmd_updater = [tooling_python, "-c", py_cmd]
            updater_succeeded = self._run_subprocess(
                cmd_updater, 20, self.project_root, self.REGISTRY_UPDATER_CLASS, verbose=False
            )

            # UNCOMMENT TO RUN THE UPDATER LOCALLY, and comment out the above few lines, requires updating riotfile.py
            # to include the following within the riot env:
            # - "filelock"
            # - "pyyaml"
            # from tests.contrib.integration_registry.registry_update_helpers.integration_registry_updater import (
            #   IntegrationRegistryUpdater
            # )
            # updater = IntegrationRegistryUpdater()
            # updater_succeeded = updater.run(data_file_path)

            # 2. Run Main IntegrationRegistry Update/Format Script if we have changes to the registry
            if updater_succeeded:
                script_path = os.path.join(self.project_root, self.MAIN_UPDATE_SCRIPT)
                if os.path.exists(script_path):
                    cmd_main = [tooling_python, script_path]
                    self._run_subprocess(cmd_main, 20, self.project_root, "Main Update Script", verbose=True)

        finally:
            # Cleanup updater's lock file
            if os.path.exists(self.updater_lock_file_path):
                try:
                    os.remove(self.updater_lock_file_path)
                except OSError:
                    pass

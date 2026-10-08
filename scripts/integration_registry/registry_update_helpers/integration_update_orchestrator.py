import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Union


class IntegrationUpdateOrchestrator:
    TOOLING_VENV_DIR = ".venv-registry-tools"
    TOOLING_DEPS = ["pyyaml", "filelock", "ruamel.yaml", "packaging"]
    REGISTRY_UPDATER_MODULE = "registry_update_helpers.integration_registry_updater"
    REGISTRY_UPDATER_CLASS = "IntegrationRegistryUpdater"
    MAIN_UPDATE_SCRIPT = "scripts/integration_registry/update_and_format_registry.py"
    LOCK_MAX_WAIT_SECONDS = 60
    # Only the exclusive-create venv setup lock needs stale-file reclamation.
    # Registry and workflow FileLock files must remain in place even when unlocked.
    STALE_LOCK_MAX_AGE_SECONDS = 120

    def __init__(self, project_root: str):
        self.project_root = project_root
        self.tooling_env_path = os.path.join(project_root, self.TOOLING_VENV_DIR)
        self.venv_lock_file_path = os.path.join(project_root, ".venv-registry-tools.lock")

    def _acquire_lock(self, lock_file_path: str) -> bool:
        start_time = time.monotonic()
        while time.monotonic() - start_time < self.LOCK_MAX_WAIT_SECONDS:
            try:
                fd = os.open(lock_file_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return True
            except FileExistsError:
                time.sleep(0.5)
            except Exception as e:
                print(f"Error acquiring lock {lock_file_path}: {e}", file=sys.stderr)
                return False
        print(f"Timeout acquiring lock {lock_file_path}", file=sys.stderr)
        return False

    def _release_lock(self, lock_file_path: str) -> None:
        try:
            os.remove(lock_file_path)
        except Exception:
            pass

    def _ensure_no_stale_lock(self, lock_file_path: str) -> None:
        try:
            age = time.time() - os.path.getmtime(lock_file_path)
        except OSError:
            return
        if age < self.STALE_LOCK_MAX_AGE_SECONDS:
            return

        # Rename so only one worker wins stale lock reclamation.
        tmp_path = lock_file_path + f".stale.{os.getpid()}"
        try:
            os.rename(lock_file_path, tmp_path)
        except OSError:
            return

        try:
            os.remove(tmp_path)
        except OSError:
            pass

    def _ensure_tooling_venv(self) -> bool:
        """Create the tooling environment once, while holding the setup lock."""
        tooling_python = os.path.join(self.tooling_env_path, "bin", "python")
        complete_path = os.path.join(self.tooling_env_path, ".complete")
        dependencies = json.dumps(self.TOOLING_DEPS)
        try:
            with open(complete_path, encoding="utf-8") as marker:
                if os.path.exists(tooling_python) and marker.read() == dependencies:
                    return True
        except FileNotFoundError:
            pass

        # AIDEV-NOTE: Reuse completed environments: another worker may already be using
        # their files after releasing the setup lock.
        if os.path.exists(self.tooling_env_path):
            shutil.rmtree(self.tooling_env_path)
        if not self._run_subprocess(
            ["python3", "-m", "venv", self.tooling_env_path], 20, self.project_root, "venv creation", verbose=False
        ):
            return False
        if not self._run_subprocess(
            [tooling_python, "-m", "pip", "install", *self.TOOLING_DEPS],
            20,
            self.project_root,
            "pip install",
            verbose=False,
        ):
            return False
        with open(complete_path, "w", encoding="utf-8") as marker:
            marker.write(dependencies)
        return True

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
            if e.stdout:
                print(e.stdout.strip(), file=sys.stderr)
            if e.stderr:
                print(e.stderr.strip(), file=sys.stderr)
            return False
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f"Error: {description} failed: {e}", file=sys.stderr)
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

    def run(self, data_file_path: str) -> bool:
        """Run one complete update workflow at a time across pytest workers."""
        self._ensure_no_stale_lock(self.venv_lock_file_path)
        if not self._acquire_lock(self.venv_lock_file_path):
            return False
        try:
            if not self._ensure_tooling_venv():
                return False
        except OSError as error:
            print(f"Error setting up integration registry environment: {error}", file=sys.stderr)
            return False
        finally:
            self._release_lock(self.venv_lock_file_path)

        integration_registry_dir = os.path.join(self.project_root, "scripts", "integration_registry")
        script_path = os.path.join(self.project_root, self.MAIN_UPDATE_SCRIPT)
        workflow_lock_path = os.path.join(integration_registry_dir, "workflow.lock")
        # Keep formatting under the workflow lock as well as the registry merge/write.
        py_cmd = f"""
import os
import subprocess
import sys
from filelock import FileLock

sys.path.insert(0, {integration_registry_dir!r})
from {self.REGISTRY_UPDATER_MODULE} import {self.REGISTRY_UPDATER_CLASS}

with FileLock({workflow_lock_path!r}, timeout=120):
    changed = {self.REGISTRY_UPDATER_CLASS}().run({data_file_path!r})
    if changed and os.path.exists({script_path!r}):
        subprocess.run([sys.executable, {script_path!r}], check=True, timeout=120)
"""
        tooling_python = os.path.join(self.tooling_env_path, "bin", "python")
        cmd = [tooling_python, "-c", py_cmd]
        return self._run_subprocess(cmd, 300, self.project_root, "Integration registry update", verbose=True)

import os
import subprocess
import time
import sys
import tempfile
import json


LOCK_FILE_NAME = ".venv-registry-tools.lock"
LOCK_MAX_WAIT_SECONDS = 60
TOOLING_VENV_DIR = ".venv-registry-tools"
TOOLING_DEPS = ["pyyaml", "riot", "filelock"]
REGISTRY_UPDATER_MODULE = "tests.contrib.integration_registry.registry_update_helpers.intgration_registry_updater"
REGISTRY_UPDATER_CLASS = "RegistryUpdater"
MAIN_UPDATE_SCRIPT = "scripts/integration_registry/update_and_format_registry.py"
UPDATER_LOCK_FILE = "ddtrace/contrib/integration_registry/registry.yaml.lock"


def acquire_lock(lock_file_path):
    start_time = time.monotonic()
    while time.monotonic() - start_time < LOCK_MAX_WAIT_SECONDS:
        try:
            fd = os.open(lock_file_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return True
        except FileExistsError:
            time.sleep(0.5)
        except Exception:
            return False
    return False

def release_lock(lock_file_path):
    try: os.remove(lock_file_path)
    except Exception: pass

def _ensure_tooling_venv(venv_path: str, project_root: str):
    tooling_python = os.path.join(venv_path, "bin", "python")
    pip_timeout = 90

    if os.path.exists(tooling_python):
        try:
            cmd = [tooling_python, "-m", "pip", "install", "-U"] + TOOLING_DEPS
            subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=project_root, timeout=pip_timeout)
            return True
        except Exception:
            return True

    try:
        cmd = ["python3", "-m", "venv", venv_path]
        subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=project_root)
    except Exception: return False

    if not os.path.exists(tooling_python): return False
    try:
        cmd = [tooling_python, "-m", "pip", "install"] + TOOLING_DEPS
        subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=project_root, timeout=pip_timeout)
        return True
    except Exception: return False

def _run_subprocess(cmd: list, timeout: int, cwd: str, description: str) -> bool:
    """Helper to run subprocess, prints stderr on failure."""
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error: {description} failed (code {e.returncode}).", file=sys.stderr)
        print(f"Stderr:\n{e.stderr}", file=sys.stderr)
        return False
    except Exception:
        return False

def export_registry_data(data: dict, request) -> str | None:
    data_file_path = None
    try:
        unique_id = f"pid{os.getpid()}"
        worker_input = getattr(request.config, "workerinput", None)
        if worker_input and "workerid" in worker_input:
             unique_id += f"_{worker_input['workerid']}"

        temp_dir = getattr(request.config, '_tmp_path_factory', None)
        base_dir = temp_dir.getbasetemp() if temp_dir else None

        fd, data_file_path = tempfile.mkstemp(
            prefix=f'registry_data_{unique_id}_', suffix='.json',
            dir=base_dir, text=True
        )
        with open(fd, 'w', encoding='utf-8') as temp_f:
            json.dump(data, temp_f)

        request.config._registry_session_data_file = data_file_path
        return data_file_path
    except Exception:
        if data_file_path and os.path.exists(data_file_path):
            try: os.remove(data_file_path)
            except OSError: pass
        if hasattr(request.config, '_registry_session_data_file'):
            delattr(request.config, '_registry_session_data_file')
        return None

def cleanup_session_data(session):
     data_file_path = getattr(session.config, '_registry_session_data_file', None)
     if data_file_path and os.path.exists(data_file_path):
         try: os.remove(data_file_path)
         except OSError: pass
     if hasattr(session.config, '_registry_session_data_file'):
         delattr(session.config, '_registry_session_data_file')


def run_update_process(project_root: str, data_file_path: str):
    """Orchestrates the registry update process and ensures lock cleanup."""
    tooling_env_path = os.path.join(project_root, TOOLING_VENV_DIR)
    venv_lock_file_path = os.path.join(project_root, LOCK_FILE_NAME)
    updater_lock_file_path = os.path.join(project_root, UPDATER_LOCK_FILE)
    venv_lock_acquired = False
    updater_succeeded = False

    try:
        # Setup Tooling Venv
        try:
            if not acquire_lock(venv_lock_file_path): return
            venv_lock_acquired = True
            if not _ensure_tooling_venv(tooling_env_path, project_root): return
        finally:
            if venv_lock_acquired: release_lock(venv_lock_file_path)

        # Run Update Process
        tooling_python = os.path.join(tooling_env_path, "bin", "python")
        if not os.path.exists(tooling_python): return

        escaped_path = data_file_path.replace("'", "'\\''")
        py_cmd = (
            f"import sys; sys.path.insert(0, '{project_root}'); "
            f"from {REGISTRY_UPDATER_MODULE} import {REGISTRY_UPDATER_CLASS}; "
            f"updater = {REGISTRY_UPDATER_CLASS}(); success = updater.run('{escaped_path}'); "
            f"sys.exit(0 if success else 1);"
        )
        cmd_updater = [tooling_python, "-c", py_cmd]
        updater_succeeded = _run_subprocess(cmd_updater, 120, project_root, "Integration Registry Updater")

        # Run Main Integration Registry Update/Format Script
        if updater_succeeded:
            script_path = os.path.join(project_root, MAIN_UPDATE_SCRIPT)
            if os.path.exists(script_path):
                cmd_main = [tooling_python, script_path]
                _run_subprocess(cmd_main, 180, project_root, "Integration Registry Update/Format Script")

    finally:
        if os.path.exists(updater_lock_file_path):
            try: os.remove(updater_lock_file_path)
            except OSError: pass

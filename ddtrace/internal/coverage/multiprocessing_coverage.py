"""Provides functionality for supporting coverage collection when using multiprocessing

This module patches the multiprocessing module to install and start coverage collection when new processes are started.

The close, join, kill, and terminate methods are patched to ensure that coverage data is consumed when the
process finishes. Programs that do not call one of these methods will not collect coverage for the processes
started via multiprocessing.

Inspired by the coverage.py multiprocessing support at
https://github.com/nedbat/coveragepy/blob/401a63bf08bdfd780b662f64d2dfe3603f2584dd/coverage/multiproc.py
"""

import json
import multiprocessing
from multiprocessing.connection import Connection
import multiprocessing.process
from pathlib import Path
import pickle  # nosec: B403  -- pickle is only used to serialize coverage data from the child process to its parent
import typing as t

from ddtrace.internal.coverage.code import ModuleCodeCollector
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


log = get_logger(__name__)

BaseProcess = multiprocessing.process.BaseProcess
base_process_bootstrap = BaseProcess._bootstrap  # type: ignore[attr-defined]
base_process_init = BaseProcess.__init__
base_process_close = BaseProcess.close
base_process_join = BaseProcess.join
base_process_terminate = BaseProcess.terminate
base_process_kill = BaseProcess.kill

DD_PATCH_ATTR = "_datadog_patch"


def _is_patched():
    return hasattr(multiprocessing, DD_PATCH_ATTR)


class CoverageCollectingMultiprocess(BaseProcess):
    def _absorb_child_coverage(self) -> None:
        if not ModuleCodeCollector.coverage_enabled() or ModuleCodeCollector._instance is None:
            return

        if self._parent_conn is None:
            log.debug("Pipe was None when absorbing child coverage data", exc_info=True)
            return

        try:
            if self._parent_conn.poll():
                rcvd = self._parent_conn.recv()
                if rcvd:
                    try:
                        data = pickle.loads(rcvd)  # nosec: B301 -- we trust this is coverage data
                    except pickle.UnpicklingError:
                        log.debug("Could not unpickle coverage data, not injecting coverage")
                        raise

                    lines: t.Dict[str, CoverageLines] = data.get("lines", {})
                    covered: t.Dict[str, CoverageLines] = data.get("covered", {})

                    ModuleCodeCollector.inject_coverage(lines, covered)
                else:
                    log.debug("Child process sent empty coverage data")
            else:
                log.debug("Child process did not send coverage data")
        except Exception:
            log.debug("Failed to absorb child coverage data", exc_info=True)

    def _bootstrap(self, *args, **kwargs) -> None:
        """Wraps around the execution of the process to collect coverage data

        Since this method executes in the child process, it is responsible for writing final coverage data back to the
        parent process. Context-based coverage is not used for processes started by this bootstrap because it is
        assumed that the parent process wants all coverage data from this child.
        """
        # Install the module code collector
        if self._dd_coverage_enabled:
            # Avoid circular import since the installer imports uses _patch_multiprocessing()
            from ddtrace.internal.coverage.installer import install

            install(include_paths=self._dd_coverage_include_paths)
            ModuleCodeCollector.start_coverage()

        # Call the original bootstrap method
        rval = base_process_bootstrap(self, *args, **kwargs)

        if self._dd_coverage_enabled and self._child_conn is not None:
            instance = ModuleCodeCollector._instance

            if instance is None:
                return

            try:
                self._child_conn.send(pickle.dumps({"lines": instance.lines, "covered": instance.covered}))
            except pickle.PicklingError:
                log.warning("Failed to pickle coverage data for child process", exc_info=True)
            except Exception:
                log.warning("Failed to send coverage data to parent process", exc_info=True)

        return rval

    def __init__(self, *posargs, **kwargs) -> None:
        self._dd_coverage_enabled = False
        self._dd_coverage_include_paths: t.List[Path] = []

        # If coverage is not enabled, the pipe used to communicate coverage data from child to parent is not needed
        self._parent_conn: t.Optional[Connection] = None
        self._child_conn: t.Optional[Connection] = None

        # Only enable coverage in a child process being created if the parent process has coverage enabled
        if ModuleCodeCollector.coverage_enabled():
            parent_conn, child_conn = multiprocessing.Pipe()
            self._parent_conn = parent_conn
            self._child_conn = child_conn

            self._dd_coverage_enabled = True
            if ModuleCodeCollector._instance is not None:
                self._dd_coverage_include_paths = ModuleCodeCollector._instance._include_paths

        base_process_init(self, *posargs, **kwargs)

    def join(self, *args, **kwargs):
        rval = base_process_join(self, *args, **kwargs)
        self._absorb_child_coverage()
        return rval

    def close(self):
        rval = base_process_close(self)
        self._absorb_child_coverage()
        return rval

    def terminate(self):
        rval = base_process_terminate(self)
        self._absorb_child_coverage()
        return rval

    def kill(self):
        rval = base_process_kill(self)
        self._absorb_child_coverage()
        return rval


class Stowaway:
    """Stowaway is unpickled as part of the child's get_preparation_data() method

    This which happens at the start of the process, which is when the ModuleCodeProcessor needs to be installed in order
    to instrument all the code being loaded in the child process.

    The _bootstrap method is called too late in the spawn or forkserver process start-up sequence and installing the
    ModuleCodeCollector in it leads to incomplete code coverage data.
    """

    def __init__(self, include_paths: t.Optional[t.List[Path]] = None, dd_coverage_enabled: bool = True) -> None:
        self.dd_coverage_enabled: bool = dd_coverage_enabled
        self.include_paths_strs: t.List[str] = []
        if include_paths is not None:
            self.include_paths_strs = [str(include_path) for include_path in include_paths]

    def __getstate__(self) -> t.Dict[str, t.Any]:
        return {
            "include_paths_strs": json.dumps(self.include_paths_strs),
            "dd_coverage_enabled": self.dd_coverage_enabled,
        }

    def __setstate__(self, state: t.Dict[str, str]) -> None:
        include_paths = [Path(include_path_str) for include_path_str in json.loads(state["include_paths_strs"])]

        if state["dd_coverage_enabled"]:
            from ddtrace.internal.coverage.installer import install

            install(include_paths=include_paths)
            _patch_multiprocessing()


def _patch_multiprocessing():
    if _is_patched():
        return

    multiprocessing.process.BaseProcess._bootstrap = CoverageCollectingMultiprocess._bootstrap
    multiprocessing.process.BaseProcess.__init__ = CoverageCollectingMultiprocess.__init__
    multiprocessing.process.BaseProcess.close = CoverageCollectingMultiprocess.close
    multiprocessing.process.BaseProcess.join = CoverageCollectingMultiprocess.join
    multiprocessing.process.BaseProcess.kill = CoverageCollectingMultiprocess.kill
    multiprocessing.process.BaseProcess.terminate = CoverageCollectingMultiprocess.terminate
    multiprocessing.process.BaseProcess._absorb_child_coverage = CoverageCollectingMultiprocess._absorb_child_coverage

    try:
        from multiprocessing import spawn

        original_get_preparation_data = spawn.get_preparation_data
    except (ImportError, AttributeError):
        pass
    else:

        def get_preparation_data_with_stowaway(name: str) -> t.Dict[str, t.Any]:
            """Make sure that the ModuleCodeCollector is installed as soon as possible, with the same include paths"""
            d = original_get_preparation_data(name)
            include_paths = (
                [] if ModuleCodeCollector._instance is None else ModuleCodeCollector._instance._include_paths
            )
            d["stowaway"] = Stowaway(include_paths=include_paths)
            return d

        spawn.get_preparation_data = get_preparation_data_with_stowaway

    setattr(multiprocessing, DD_PATCH_ATTR, True)

from ctypes import CDLL
from ctypes import POINTER
from ctypes import byref
from ctypes import c_bool
import os
import platform
import sys
import threading

from ddtrace.internal import atexit
from ddtrace.internal import forksafe
from ddtrace.internal.telemetry.data import get_dependencies
from ddtrace.internal.telemetry.data import get_host_info
from ddtrace.internal.telemetry.libdd.libdd_ctypes import Builder
from ddtrace.internal.telemetry.libdd.libdd_ctypes import CharSlice
from ddtrace.internal.telemetry.libdd.libdd_ctypes import Handler
from ddtrace.internal.telemetry.libdd.libdd_ctypes import ddog_LogLevel
from ddtrace.internal.telemetry.libdd.libdd_ctypes import ddog_MaybeError
from ddtrace.internal.telemetry.libdd.libdd_ctypes import ddog_Option_Bool
from ddtrace.settings import _config as config
from ddtrace.version import get_version


def str_to_charslice(str_val):
    bytes_val = str.encode(str_val)
    sn_struct = CharSlice()
    sn_struct.ptr = bytes_val
    sn_struct.len = len(bytes_val)
    return sn_struct


def bool_to_optionbool(bool_val):
    ob = ddog_Option_Bool()
    ob.ddog_Option_Bool_0.ddog_Option_Bool_0_0.some = c_bool(bool_val)
    return ob


class LibddAPI:
    DIR = os.path.dirname(__file__)

    def __init__(self):
        self._libdd = CDLL(os.path.join(self.DIR, "libddtelemetry.dylib"))
        self._lock = threading.Lock()

    # ddog_MaybeError ddog_builder_instantiate(struct ddog_TelemetryWorkerBuilder **builder,
    #                                                        ddog_CharSlice service_name,
    #                                                        ddog_CharSlice language_name,
    #                                                        ddog_CharSlice language_version,
    #
    #                                                        ddog_CharSlice tracer_version);
    def ddog_builder_instantiate(self, builder, service, language, language_verion, ddtrace_version):
        self._libdd.ddog_builder_instantiate.restype = ddog_MaybeError
        return self._libdd.ddog_builder_instantiate(
            byref(builder),
            str_to_charslice(service),
            str_to_charslice(language),
            str_to_charslice(language_verion),
            str_to_charslice(ddtrace_version),
        )

    def ddog_builder_with_bool_config_telemetry_debug_logging_enabled(self, builder, debug):
        self._libdd.ddog_builder_with_bool_config_telemetry_debug_logging_enabled(builder, c_bool(debug))

    def ddog_builder_with_path_config_mock_client_file(self, builder, filename):
        # send telemetry events to file
        return self._libdd.ddog_builder_with_path_config_mock_client_file(builder, str_to_charslice(filename))

    # ddog_MaybeError ddog_builder_with_native_deps(struct ddog_TelemetryWorkerBuilder *builder,
    #                                               bool include_native_deps)
    def ddog_builder_with_native_deps(self, builder, use_native_deps):
        self._libdd.ddog_builder_with_native_deps.restype = ddog_MaybeError
        return self._libdd.ddog_builder_with_native_deps(builder, c_bool(use_native_deps))

    # ddog_MaybeError ddog_builder_with_rust_shared_lib_deps(struct ddog_TelemetryWorkerBuilder *builder,
    #                                                    bool include_rust_shared_lib_deps);
    def ddog_builder_with_rust_shared_lib_deps(self, builder, use_native_deps):
        # Do not include rust shared library deps
        self._libdd.ddog_builder_with_rust_shared_lib_deps.restype = ddog_MaybeError
        return self._libdd.ddog_builder_with_rust_shared_lib_deps(builder, c_bool(use_native_deps))

    def ddog_builder_with_str_named_property(self, builder, prop, value):
        self._libdd.ddog_builder_with_str_named_property.restype = ddog_MaybeError
        return self._libdd.ddog_builder_with_str_named_property(
            builder, str_to_charslice(prop), str_to_charslice(value)
        )

    # ddog_MaybeError ddog_builder_run(struct ddog_TelemetryWorkerBuilder *builder,
    #                                  struct ddog_TelemetryWorkerHandle **handle);
    # starts worker
    def ddog_builder_run(self, builder, handler):
        self._libdd.ddog_builder_run.restype = ddog_MaybeError
        return self._libdd.ddog_builder_run(builder, byref(handler))

    # ddog_MaybeError ddog_handle_start(const struct ddog_TelemetryWorkerHandle *handle);
    def ddog_handle_start(self, handler):
        self._libdd.ddog_handle_start.restype = ddog_MaybeError
        # sends app started
        return self._libdd.ddog_handle_start(handler)

    # ddog_MaybeError ddog_handle_stop(const struct ddog_TelemetryWorkerHandle *handle);
    def ddog_handle_stop(self, handler):
        self._libdd.ddog_handle_stop.restype = ddog_MaybeError
        return self._libdd.ddog_handle_stop(handler)

    # ddog_handle_wait_for_shutdown
    def ddog_handle_wait_for_shutdown(self, handler):
        self._libdd.ddog_handle_wait_for_shutdown.restype = ddog_MaybeError
        return self._libdd.ddog_handle_wait_for_shutdown(handler)

    # ddog_MaybeError ddog_handle_add_dependency(const struct ddog_TelemetryWorkerHandle *handle,
    #                                            ddog_CharSlice dependency_name,
    #                                            ddog_CharSlice dependency_version);
    def ddog_handle_add_dependency(self, handler, name, version):
        self._libdd.ddog_handle_add_dependency.restype = ddog_MaybeError
        return self._libdd.ddog_handle_add_dependency(
            handler,
            str_to_charslice(name),
            str_to_charslice(version),
        )

    # ddog_MaybeError ddog_handle_add_integration(const struct ddog_TelemetryWorkerHandle *handle,
    #                                             ddog_CharSlice dependency_name,
    #                                             ddog_CharSlice dependency_version,
    #                                             struct ddog_Option_Bool compatible,
    #                                             struct ddog_Option_Bool enabled,
    #                                             struct ddog_Option_Bool auto_enabled);
    def ddog_handle_add_integration(self, handler, name, version, compatible, enabled, auto_enabled):
        self._libdd.ddog_handle_add_integration.restype = ddog_MaybeError
        return self._libdd.ddog_handle_add_integration(
            handler,
            str_to_charslice(name),
            str_to_charslice(version),
            bool_to_optionbool(compatible),
            bool_to_optionbool(enabled),
            bool_to_optionbool(auto_enabled),
        )

    # ddog_MaybeError ddog_handle_add_log(const struct ddog_TelemetryWorkerHandle *handle,
    #                                     ddog_CharSlice identifier,
    #                                     ddog_CharSlice message,
    #                                     enum ddog_LogLevel level,
    #                                     ddog_CharSlice stack_trace);
    def ddog_handle_add_log(self, handler, identifier, message, level, stack_trace):
        self._libdd.ddog_handle_add_log.restype = ddog_MaybeError
        return self._libdd.ddog_handle_add_log(
            handler,
            str_to_charslice(identifier),
            str_to_charslice(message),
            ddog_LogLevel(level),
            str_to_charslice(stack_trace),
        )

    # ddog_MaybeError ddog_builder_with_config(struct ddog_TelemetryWorkerBuilder *builder,
    #                                      ddog_CharSlice name,
    #                                      ddog_CharSlice value);
    def ddog_builder_with_config(self, builder, name, value):
        self._libdd.ddog_builder_with_config.restype = ddog_MaybeError
        return self._libdd.ddog_builder_with_config(
            builder,
            str_to_charslice(name),
            str_to_charslice(value),
        )


class LibDDTelemetry:
    # assumes macos, this will fail on other platforms
    DIR = os.path.dirname(__file__)
    FILENAME = os.path.join(DIR, "dumpfile.json")

    def __init__(self):
        self.libddapi = LibddAPI()
        self.builder = self._start_libdd_builder()
        self.handler = self._start_handler(self.builder)
        self.forked = False
        forksafe.register(self._fork_worker)

    def _start_libdd_builder(self):
        builder = POINTER(Builder)()

        err = self.libddapi.ddog_builder_instantiate(
            builder,
            config.service or "",
            "python",
            ".".join(map(str, sys.version_info[:3])),
            get_version(),
        )
        assert err.tag == 1

        if os.environ.get("DD_TELEMETRY_DEBUG", "false").lower() in ("true", "t", "1"):
            # enable debug logging
            self.libddapi.ddog_builder_with_bool_config_telemetry_debug_logging_enabled(builder, True)

        # send telemetry events to file
        self.libddapi.ddog_builder_with_path_config_mock_client_file(builder, self.FILENAME)

        # Do not include native deps
        err = self.libddapi.ddog_builder_with_native_deps(builder, False)
        # Do not include rust shared library deps
        err = self.libddapi.ddog_builder_with_rust_shared_lib_deps(builder, False)
        # Update properties
        err = self.libddapi.ddog_builder_with_str_named_property(builder, "application.env", config.env or "")
        err = self.libddapi.ddog_builder_with_str_named_property(
            builder, "application.runtime_name", platform.python_implementation()
        )
        host = get_host_info()
        err = self.libddapi.ddog_builder_with_str_named_property(builder, "host.kernel_name", host["kernel_name"])
        err = self.libddapi.ddog_builder_with_str_named_property(builder, "host.kernel_release", host["kernel_name"])
        err = self.libddapi.ddog_builder_with_str_named_property(builder, "host.kernel_version", host["kernel_version"])
        return builder

    def _start_handler(self, builder):
        handler = POINTER(Handler)()
        self.libddapi.ddog_builder_run(builder, handler)
        return handler

    def enable(self):
        self._dependencies_loaded()
        self._app_started()
        atexit.register(self._app_closed)

    def disable(self):
        pass

    def _fork_worker(self):
        # revisit forking
        # self.builder = self._start_libdd_builder()
        # self.handler = self._start_handler(self.builder)
        self.forked = True

    def _app_started(self):
        if self.forked:
            return
        # sends app started
        err = self.libddapi.ddog_handle_start(self.handler)
        return err.tag

    def _app_closed(self):
        if self.forked:
            return
        err = self.libddapi.ddog_handle_stop(self.handler)
        err = self.libddapi.ddog_handle_wait_for_shutdown(self.handler)
        return err.tag

    def _dependencies_loaded(self):
        for dep in get_dependencies():
            self.add_dependency(dep["name"], dep["version"])

    def add_dependency(self, name, version):
        err = self.libddapi.ddog_handle_add_dependency(self.handler, name, version)
        return err.tag

    def add_integration(self, name, auto_enabled=True):
        err = self.libddapi.ddog_handle_add_integration(
            self.handler,
            name,
            "",
            True,
            True,
            auto_enabled,
        )
        return err.tag

    def add_log(self, identifier, message, level, stack_trace):
        # error=2, warn=1, debug=0
        assert level in (0, 1, 2)

        err = self.libddapi.ddog_handle_add_log(
            self.handler,
            identifier,
            message,
            level,
            stack_trace,
        )
        return err.tag

    def add_config(self, name, value):
        err = self.libddapi.ddog_builder_with_config(self.builder, name, value)
        return err.tag


def test_case():
    k = LibDDTelemetry()
    k.enable()
    # k.add_integration("moon_int", True)
    # k.add_config("DD_TRACE_IS_AMAZING", "true")
    os.fork()
    print(k.forked)


if __name__ == "__main__":
    # Uncomment test_case with DD_SERVICE=.... DD_ENV=...  [file]
    test_case()

# distutils: language = c++
# cython: language_level=3

from functools import wraps

def not_implemented(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ImportError:
            raise NotImplementedError(f"{func.__name__} is not implemented")
    return wrapper


cdef extern from "<string_view>" namespace "std" nogil:
    cdef cppclass string_view:
        string_view(const char* s, size_t count)

# For now, the crashtracker code is bundled in the libdatadog Profiling FFI.
# This is primarily to reduce binary size.
cdef extern from "interface.hpp":
    void crashtracker_set_url(string_view url);
    void crashtracker_set_service(string_view service);
    void crashtracker_set_env(string_view env);
    void crashtracker_set_version(string_view version);
    void crashtracker_set_runtime(string_view runtime);
    void crashtracker_set_runtime_version(string_view runtime_version);
    void crashtracker_set_library_version(string_view profiler_version);
    void crashtracker_set_stdout_filename(string_view filename);
    void crashtracker_set_stderr_filename(string_view filename);
    void crashtracker_set_alt_stack(bool alt_stack);
    void crashtracker_set_resolve_frames_disable();
    void crashtracker_set_resolve_frames_fast();
    void crashtracker_set_resolve_frames_full();
    bint crashtracker_set_receiver_binary_path(string_view path);
    void crashtracker_start();


@not_implemented
def set_url(url: StringType) -> None:
    url_bytes = ensure_binary_or_empty(url)
    crashtracker_set_url(string_view(<const char*>url_bytes, len(url_bytes)))


@not_implemented
def set_service(service: StringType) -> None:
    service_bytes = ensure_binary_or_empty(service)
    crashtracker_set_service(string_view(<const char*>service_bytes, len(service_bytes)))


@not_implemented
def set_env(env: StringType) -> None:
    env_bytes = ensure_binary_or_empty(env)
    crashtracker_set_env(string_view(<const char*>env_bytes, len(env_bytes)))


@not_implemented
def set_version(version: StringType) -> None:
    version_bytes = ensure_binary_or_empty(version)
    crashtracker_set_version(string_view(<const char*>version_bytes, len(version_bytes)))


@not_implemented
def set_runtime(runtime: StringType) -> None:
    runtime_bytes = ensure_binary_or_empty(runtime)
    crashtracker_set_runtime(string_view(<const char*>runtime_bytes, len(runtime_bytes)))


@not_implemented
def set_runtime_version(runtime_version: StringType) -> None:
    runtime_version_bytes = ensure_binary_or_empty(runtime_version)
    crashtracker_set_runtime_version(string_view(<const char*>runtime_version_bytes, len(runtime_version_bytes)))


@not_implemented
def set_library_version(library_version: StringType) -> None:
    library_version_bytes = ensure_binary_or_empty(library_version)
    crashtracker_set_library_version(string_view(<const char*>library_version_bytes, len(library_version_bytes)))


@not_implemented
def set_stdout_filename(filename: StringType) -> None:
    filename_bytes = ensure_binary_or_empty(filename)
    crashtracker_set_stdout_filename(string_view(<const char*>filename_bytes, len(filename_bytes)))


@not_implemented
def set_stderr_filename(filename: StringType) -> None:
    filename_bytes = ensure_binary_or_empty(filename)
    crashtracker_set_stderr_filename(string_view(<const char*>filename_bytes, len(filename_bytes)))


@not_implemented
def set_alt_stack(alt_stack: bool) -> None:
    crashtracker_set_alt_stack(alt_stack)


@not_implemented
def set_resolve_frames_disable() -> None:
    crashtracker_set_resolve_frames_disable()


@not_implemented
def set_resolve_frames_fast() -> None:
    crashtracker_set_resolve_frames_fast()


@not_implemented
def set_resolve_frames_full() -> None:
    crashtracker_set_resolve_frames_full()


@not_implemented
def start() -> None:
    # The file is "crashtracker_exe" in the same directory as this .so
    # TODO this is all wrong
    exe_dir = os.path.dirname(__file__)
    crashtracker_path = os.path.join(exe_dir, "crashtracker_exe")
    crashtracker_path_bytes = ensure_binary_or_empty(crashtracker_path)
    bin_exists = crashtracker_set_receiver_binary_path(
        string_view(<const char*>crashtracker_path_bytes, len(crashtracker_path_bytes))
    )

    # We don't have a good place to report on the failure for now.
    if bin_exists:
        crashtracker_start()

# distutils: language = c++
# cython: language_level=3

cdef extern from "<string_view>" namespace "std" nogil:
    cdef cppclass string_view:
        string_view(const char* s, size_t count)

# For now, the crashtracker code is bundled in the libdatadog Profiling FFI.
# This is primarily to reduce binary size.
cdef extern from "interface.hpp":
    void ddup_config_crashtracker_url(string_view url)
    void ddup_config_crashtracker_stdout_filename(string_view filename)
    void ddup_config_crashtracker_stderr_filename(string_view filename)
    void ddup_config_crashtracker_alt_stack(bint alt_stack)
    void ddup_config_crashtracker_resolve_frames_disable()
    void ddup_config_crashtracker_resolve_frames_fast()
    void ddup_config_crashtracker_resolve_frames_full()
    bint ddup_config_crashtracker_receiver_binary_path(string_view path)
    void ddup_crashtracker_start()

def set_crashtracker_url(url: StringType) -> None:
    url_bytes = ensure_binary_or_empty(url)
    ddup_config_crashtracker_url(string_view(<const char*>url_bytes, len(url_bytes)))


def set_crashtracker_stdout_filename(filename: StringType) -> None:
    filename_bytes = ensure_binary_or_empty(filename)
    ddup_config_crashtracker_stdout_filename(string_view(<const char*>filename_bytes, len(filename_bytes)))


def set_crashtracker_stderr_filename(filename: StringType) -> None:
    filename_bytes = ensure_binary_or_empty(filename)
    ddup_config_crashtracker_stderr_filename(string_view(<const char*>filename_bytes, len(filename_bytes)))


def set_crashtracker_alt_stack(alt_stack: bool) -> None:
    ddup_config_crashtracker_alt_stack(alt_stack)


def set_crashtracker_resolve_frames_disable() -> None:
    ddup_config_crashtracker_resolve_frames_disable()


def set_crashtracker_resolve_frames_fast() -> None:
    ddup_config_crashtracker_resolve_frames_fast()


def set_crashtracker_resolve_frames_full() -> None:
    ddup_config_crashtracker_resolve_frames_full()


def start_crashtracker() -> None:
    # The file is "crashtracker_exe" in the same directory as this .so
    exe_dir = os.path.dirname(__file__)
    crashtracker_path = os.path.join(exe_dir, "crashtracker_exe")
    crashtracker_path_bytes = ensure_binary_or_empty(crashtracker_path)
    bin_exists = ddup_config_crashtracker_receiver_binary_path(
        string_view(<const char*>crashtracker_path_bytes, len(crashtracker_path_bytes))
    )

    # We don't have a good place to report on the failure for now.
    if bin_exists:
        ddup_crashtracker_start()



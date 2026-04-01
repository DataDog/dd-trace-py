import os
import time

import _build.constants as _c


class DebugMetadata:
    start_ns = 0
    enabled = "_DD_DEBUG_EXT" in os.environ
    metadata_file = os.getenv("_DD_DEBUG_EXT_FILE", "debug_ext_metadata.txt")
    build_times = {}
    download_times = {}

    @classmethod
    def dump_metadata(cls):
        if not cls.enabled or not cls.build_times:
            return

        total_ns = time.time_ns() - cls.start_ns
        total_s = total_ns / 1e9

        build_total_ns = sum(cls.build_times.values())
        build_total_s = build_total_ns / 1e9
        build_percent = (build_total_ns / total_ns) * 100.0

        # Lazy import to avoid circular dependency at module level
        from _build.extensions import CustomBuildExt

        with open(cls.metadata_file, "w") as f:
            f.write(f"Total time: {total_s:0.2f}s\n")
            f.write("Environment:\n")
            for n, v in [
                ("CARGO_BUILD_JOBS", os.getenv("CARGO_BUILD_JOBS", "unset")),
                ("CMAKE_BUILD_PARALLEL_LEVEL", os.getenv("CMAKE_BUILD_PARALLEL_LEVEL", "unset")),
                ("DD_COMPILE_MODE", _c.COMPILE_MODE),
                ("DD_USE_SCCACHE", _c.SCCACHE_COMPILE),
                ("DD_FAST_BUILD", _c.FAST_BUILD),
                ("DD_CMAKE_INCREMENTAL_BUILD", CustomBuildExt.INCREMENTAL),
            ]:
                print(f"\t{n}: {v}", file=f)
            f.write("Extension build times:\n")
            f.write(f"\tTotal: {build_total_s:0.2f}s ({build_percent:0.2f}%)\n")
            for ext, elapsed_ns in sorted(cls.build_times.items(), key=lambda x: x[1], reverse=True):
                elapsed_s = elapsed_ns / 1e9
                ext_percent = (elapsed_ns / total_ns) * 100.0
                f.write(f"\t{ext.name}: {elapsed_s:0.2f}s ({ext_percent:0.2f}%)\n")

            if cls.download_times:
                download_total_ns = sum(cls.download_times.values())
                download_total_s = download_total_ns / 1e9
                download_percent = (download_total_ns / total_ns) * 100.0

                f.write("Artifact download times:\n")
                f.write(f"\tTotal: {download_total_s:0.2f}s ({download_percent:0.2f}%)\n")
                for n, elapsed_ns in sorted(cls.download_times.items(), key=lambda x: x[1], reverse=True):
                    elapsed_s = elapsed_ns / 1e9
                    ext_percent = (elapsed_ns / total_ns) * 100.0
                    f.write(f"\t{n}: {elapsed_s:0.2f}s ({ext_percent:0.2f}%)\n")


def debug_build_extension(fn):
    def wrapper(self, ext, *args, **kwargs):
        start = time.time_ns()
        try:
            return fn(self, ext, *args, **kwargs)
        finally:
            DebugMetadata.build_times[ext] = time.time_ns() - start

    return wrapper

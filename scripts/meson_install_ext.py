#!/usr/bin/env python3
"""
meson_install_ext.py — called by meson.add_install_script() to copy
the complex-build extensions (Rust, CMake) from their unique intermediate
names in the meson build directory to their final Python-importable names
under the install prefix.

Environment variables provided by meson at install time:
  MESON_BUILD_ROOT          — absolute path to the meson build directory
  MESON_SOURCE_ROOT         — absolute path to the source root
  MESON_INSTALL_PREFIX      — install prefix
  MESON_INSTALL_DESTDIR_PREFIX — DESTDIR + prefix (use this for actual file paths)
"""

import argparse
import os
from pathlib import Path
import shutil


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--ext-suffix", required=True)
    p.add_argument("--py-install-dir", required=True)
    p.add_argument("--is-unix", default="1")
    p.add_argument("--is-64bit", default="1")
    p.add_argument("--host", default="")
    return p.parse_args()


def main():
    args = parse_args()

    build_root = Path(os.environ["MESON_BUILD_ROOT"])
    dest_prefix = Path(os.environ["MESON_INSTALL_DESTDIR_PREFIX"])
    py_dir = Path(args.py_install_dir)
    ext = args.ext_suffix
    is_unix = args.is_unix == "1"
    is_64bit = args.is_64bit == "1"
    host = args.host.lower()

    def copy_ext(src_rel, dst_rel):
        """Copy a built extension from build dir to the installed location."""
        src = build_root / src_rel
        dst = dest_prefix / py_dir / dst_rel

        if not src.exists():
            print(f"[meson_install_ext] WARNING: source not found: {src}")
            return False

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"[meson_install_ext] Installed: {dst_rel}")
        return True

    # ---- Rust extension: _native ----
    copy_ext(
        f"_native_rust/native_rust_raw{ext}",
        f"ddtrace/internal/native/_native{ext}",
    )

    if is_unix and is_64bit:
        # ---- libdd_wrapper ----
        copy_ext(
            f"libdd_wrapper/libdd_wrapper_raw{ext}",
            f"ddtrace/internal/datadog/profiling/libdd_wrapper{ext}",
        )

        # ---- _memalloc ----
        copy_ext(
            f"_memalloc/memalloc_raw{ext}",
            f"ddtrace/profiling/collector/_memalloc{ext}",
        )

        # ---- _ddup ----
        copy_ext(
            f"_ddup/ddup_raw{ext}",
            f"ddtrace/internal/datadog/profiling/ddup/_ddup{ext}",
        )

        # ---- _stack ----
        copy_ext(
            f"_stack/stack_raw{ext}",
            f"ddtrace/internal/datadog/profiling/stack/_stack{ext}",
        )

    # ---- IAST taint tracking: _native ----
    copy_ext(
        f"_iast_native/native_iast_raw{ext}",
        f"ddtrace/appsec/_iast/_taint_tracking/_native{ext}",
    )

    # ---- psutil vendor ----
    if host == "darwin":
        psutil_name = f"_psutil_osx{ext}"
    elif host == "linux":
        psutil_name = f"_psutil_linux{ext}"
    elif host == "windows":
        psutil_name = f"_psutil_windows{ext}"
    else:
        psutil_name = None

    if psutil_name:
        copy_ext(
            f"psutil_vendor/{psutil_name}",
            f"ddtrace/vendor/psutil/{psutil_name}",
        )

    # ---- libddwaf binaries ----
    # libddwaf is downloaded by the custom_target into the SOURCE tree.
    # install_subdir('ddtrace', ...) should have picked them up already.
    # If not, we copy them here as a fallback.
    source_root = Path(os.environ.get("MESON_SOURCE_ROOT", ""))
    libddwaf_src = source_root / "ddtrace/appsec/_ddwaf/libddwaf"
    if libddwaf_src.exists():
        libddwaf_dst = dest_prefix / py_dir / "ddtrace/appsec/_ddwaf/libddwaf"
        if not libddwaf_dst.exists():
            shutil.copytree(libddwaf_src, libddwaf_dst)
            print(f"[meson_install_ext] Installed libddwaf: {libddwaf_dst}")


if __name__ == "__main__":
    main()

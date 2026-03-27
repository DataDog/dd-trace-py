#!/usr/bin/env python3
"""
Install-script helper for the IAST native module.

The Meson build graph needs the raw build artifact to have a unique name so it
does not collide with ddtrace.internal.native._native, but the installed Python
package still needs the canonical `_native${EXT_SUFFIX}` filename.
"""

import argparse
import os
from pathlib import Path
import shutil


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ext-suffix", required=True)
    parser.add_argument("--py-install-dir", required=True)
    return parser.parse_args()


def main():
    args = parse_args()

    build_root = Path(os.environ["MESON_BUILD_ROOT"])
    dest_prefix = Path(os.environ["MESON_INSTALL_DESTDIR_PREFIX"])
    py_dir = Path(args.py_install_dir)
    ext = args.ext_suffix

    raw_src = build_root / f"native_iast_raw{ext}"
    if not raw_src.exists():
        print(f"[meson_install_iast_native] WARNING: source not found: {raw_src}")
        return

    final_dst = dest_prefix / py_dir / "ddtrace/appsec/_iast/_taint_tracking" / f"_native{ext}"
    final_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(raw_src, final_dst)
    print(f"[meson_install_iast_native] Installed: {final_dst}")

    staging_file = dest_prefix / py_dir / ".meson-native" / f"native_iast_raw{ext}"
    if staging_file.exists():
        staging_file.unlink()
    staging_dir = staging_file.parent
    try:
        staging_dir.rmdir()
    except OSError:
        pass


if __name__ == "__main__":
    main()

"""Copy meson-built Cython/native extensions from the build directory into the source tree.

AIDEV-NOTE: With the meson-python editable install, compiled extensions (e.g.
_encoding.cpython-39.so) live exclusively in build/mesonpy-*/ and are served to
importers via MesonpyMetaFinder in sys.meta_path.  Tests that spawn subprocesses
using system Python interpreters (without access to the riot venv's site-packages
and therefore without MesonpyMetaFinder) cannot import ddtrace because the
extensions are not in the source tree.  Copying them here restores the behavior
that cmake/setuptools provided (where extensions lived in ddtrace/ after an
in-place build) and allows the existing ddtrace/**/*.so* artifact glob to capture
them.
"""

import importlib.machinery
import json
import os
import pathlib
import shutil


ext_suffixes = tuple(importlib.machinery.EXTENSION_SUFFIXES)
copied = 0

for build_dir in pathlib.Path("build").glob("mesonpy-*/"):
    plan_path = build_dir / "meson-info" / "intro-install_plan.json"
    if not plan_path.exists():
        continue
    with open(plan_path, encoding="utf-8") as fh:
        plan = json.load(fh)
    for _section, data in plan.items():
        for src, target_info in data.items():
            dest = target_info.get("destination", "")
            parts = pathlib.PurePosixPath(dest).parts
            if not parts or parts[0] not in ("{py_platlib}", "{py_purelib}"):
                continue
            if len(parts) < 2 or parts[1] != "ddtrace":
                continue
            if not any(src.endswith(s) for s in ext_suffixes):
                continue
            rel = os.path.join(*parts[1:])  # e.g. ddtrace/internal/_encoding.cpython-39.so
            dst = pathlib.Path(rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1

print("Copied " + str(copied) + " meson extension(s) into source tree.", flush=True)

"""Native wheels discard obsolete WAF packages from incremental staging."""

import os
from pathlib import Path
import shutil

import pytest


SETUP_PY = Path(__file__).resolve().parents[2] / "setup.py"


def _build_py(tmp_path):
    """Exec the build_py subclass of setup.py with a stub setuptools base."""
    source = SETUP_PY.read_text()
    code = source[source.index("class CustomBuildPy(BuildPyCommand):") : source.index("class CleanLibraries(")]
    here = tmp_path / "source"
    (here / "ddtrace").mkdir(parents=True)
    libddwaf_dir = here / "ddtrace" / "appsec" / "_ddwaf"

    class StubBuildPy:
        """Copies into build_lib the way setuptools does: never removing anything."""

        editable_mode = False
        user_options = []
        boolean_options = []

        def initialize_options(self):
            pass

        def run(self):
            shutil.copytree(here / "ddtrace", Path(self.build_lib) / "ddtrace", dirs_exist_ok=True)

    namespace = {
        "os": os,
        "shutil": shutil,
        "Path": Path,
        "BuildPyCommand": StubBuildPy,
        "CustomBuildExt": type("CustomBuildExt", (), {"INCREMENTAL": True}),
        "CleanLibraries": type("CleanLibraries", (), {"remove_artifacts": staticmethod(lambda: None)}),
        "HERE": here,
        "OBSOLETE_WAF_DIR": libddwaf_dir,
        "IS_EDITABLE": False,
        "_WHEEL_EXCLUDED_EXTENSIONS": frozenset([".c"]),
    }
    exec(code, namespace)  # noqa: S102
    builder = namespace["CustomBuildPy"]()
    builder.build_lib = str(tmp_path / "build" / "lib")
    return builder, libddwaf_dir


def _staged(builder):
    staged = Path(builder.build_lib) / "ddtrace" / "appsec" / "_ddwaf"
    return sorted(p.name for p in staged.rglob("*") if p.is_file())


@pytest.mark.parametrize("library", ["libddwaf.so", "libddwaf.dylib", "ddwaf.dll"])
def test_native_wheels_remove_obsolete_package(tmp_path, library):
    builder, libddwaf_dir = _build_py(tmp_path)
    libddwaf_dir.mkdir(parents=True)
    (libddwaf_dir / library).write_bytes(b"old source library")
    staged = Path(builder.build_lib) / "ddtrace/appsec/_ddwaf/libddwaf/x64/lib"
    staged.mkdir(parents=True)
    (staged / "libddwaf.dll").write_bytes(b"old staged DLL")
    obsolete = staged.parents[2]
    (obsolete / "__init__.py").write_text("old binding package")
    (obsolete / "waf.py").write_text("old binding implementation")
    builder.run()
    assert not _staged(builder)
    assert not libddwaf_dir.exists()

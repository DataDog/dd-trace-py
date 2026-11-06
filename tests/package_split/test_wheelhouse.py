"""Wheel parsing and inspection helpers, exercised on hand-crafted wheels."""

from __future__ import annotations

from package_split import wheelhouse
import pytest

from ._support import craft_wheel
from ._support import package_files


def test_read_wheel_metadata_and_entry_points(tmp_path):
    wheel = craft_wheel(
        tmp_path,
        "sample-pkg",
        "1.2.3",
        package_files("sample_pkg", **{"__init__.py": "__version__ = '1.2.3'\n"}),
        requires=["wrapt>=1,<3"],
        entry_points={"console_scripts": {"sample-run": "sample_pkg:main"}},
    )
    info = wheelhouse.read_wheel(wheel)
    assert (info.name, info.version) == ("sample-pkg", "1.2.3")
    assert info.metadata["requires-dist"] == ["wrapt>=1,<3"]
    assert info.entry_points["console_scripts"]["sample-run"] == "sample_pkg:main"
    assert "sample_pkg/__init__.py" in info.files
    assert info.package_files() == ["sample_pkg/__init__.py"]


def test_native_file_detection(tmp_path):
    wheel = craft_wheel(
        tmp_path,
        "native-thing",
        "1.0.0",
        {
            "native_thing/__init__.py": "",
            "native_thing/native_mod.cpython-312-darwin.so": "\x00\x01binary",
        },
    )
    info = wheelhouse.read_wheel(wheel)
    assert wheelhouse.wheel_native_files(info) == ["native_thing/native_mod.cpython-312-darwin.so"]


def test_is_native_file_by_suffix():
    assert wheelhouse.is_native_file("x/libfoo.so")
    assert wheelhouse.is_native_file("x/mod.pyd")
    assert wheelhouse.is_native_file("x/lib.dylib")
    assert not wheelhouse.is_native_file("x/mod.py")
    assert not wheelhouse.is_native_file("x/data.txt")
    assert not wheelhouse.is_native_file("x/module.pyi")


def test_unpack_wheel_rejects_unsafe_members(tmp_path):
    import zipfile

    evil = tmp_path / "evil-1.0-py3-none-any.whl"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../../escape.py", "x = 1")
    with pytest.raises(wheelhouse.BuildError):
        wheelhouse.unpack_wheel(evil, tmp_path / "out")

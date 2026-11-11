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


def _fake_site_packages(venv_dir):
    # Mirror _site_packages() so distribution_record_files finds the
    # hand-crafted dist-infos without a real venv.
    site = venv_dir / "lib" / ("python%s.%s" % __import__("sys").version_info[:2]) / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    return site


def test_distribution_record_files_skips_no_record_outside_required(tmp_path):
    # uv workspaces install a virtual root distribution (no RECORD, owns no
    # files) alongside the package; blanket fail-closed made the artifacts
    # gate fail even when every sub-check passed (trial F15).
    site = _fake_site_packages(tmp_path)
    dist = site / "sample-1.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: sample\nVersion: 1.0\n")
    (dist / "RECORD").write_text("sample/__init__.py,,\nsample/mod.py,,\n")
    virtual = site / "datadog_apm_python_workspace-0.dist-info"
    virtual.mkdir()
    (virtual / "METADATA").write_text("Metadata-Version: 2.1\nName: datadog-apm-python-workspace\nVersion: 0\n")

    installed = {"sample": "1.0", "datadog-apm-python-workspace": "0"}
    owned = wheelhouse.distribution_record_files(tmp_path, installed, required={"sample"})
    assert owned == {"sample": ["sample/__init__.py", "sample/mod.py"]}


def test_distribution_record_files_fails_closed_for_required(tmp_path):
    site = _fake_site_packages(tmp_path)
    dist = site / "sample-1.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: sample\nVersion: 1.0\n")

    with pytest.raises(wheelhouse.BuildError, match="cannot verify sample"):
        wheelhouse.distribution_record_files(tmp_path, {"sample": "1.0"}, required={"sample"})

"""Tests for scripts/update-libddwaf-checksums.py."""

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "update-libddwaf-checksums.py"
SETUP_PY = SCRIPT.parents[1] / "setup.py"
_SPEC = importlib.util.spec_from_file_location("update_libddwaf_checksums", SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
updater = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(updater)


def _checksum(name):
    return hashlib.sha256(name.encode()).hexdigest()


def _release(version):
    """Responses of a stub GitHub release of libddwaf with every archive the build bundles."""
    current, checksums = updater.pinned(SETUP_PY.read_text())
    responses = {}
    assets = []
    for name in updater.archive_names(checksums, current, version):
        url = f"https://github.invalid/{name}"
        assets.append({"name": name, "digest": f"sha256:{_checksum(name)}", "browser_download_url": url})
        assets.append({"name": f"{name}.sha256", "digest": None, "browser_download_url": f"{url}.sha256"})
        responses[f"{url}.sha256"] = f"{_checksum(name)}  {name}\n".encode()
    responses[updater.RELEASE_API % version] = json.dumps({"assets": assets}).encode()
    return responses


def _setup_py(tmp_path):
    copy = tmp_path / "setup.py"
    shutil.copyfile(SETUP_PY, copy)
    return copy


def _release_assets(responses, version):
    return json.loads(responses[updater.RELEASE_API % version])["assets"]


def _set_release_assets(responses, version, assets):
    responses[updater.RELEASE_API % version] = json.dumps({"assets": assets}).encode()


def test_pinning_the_current_values_leaves_setup_py_unchanged():
    source = SETUP_PY.read_text()

    assert updater.updated_setup_py(source, *updater.pinned(source)) == source


def test_a_new_release_is_pinned_with_its_checksums(tmp_path):
    setup_py = _setup_py(tmp_path)
    current, checksums = updater.pinned(setup_py.read_text())

    updater.main(["9.9.9"], fetch=_release("9.9.9").__getitem__, setup_py=setup_py)

    version, new_checksums = updater.pinned(setup_py.read_text())
    assert version == "9.9.9"
    assert new_checksums == {name: _checksum(name) for name in updater.archive_names(checksums, current, "9.9.9")}
    assert updater.updated_setup_py(setup_py.read_text(), current, checksums) == SETUP_PY.read_text()


def test_a_digest_not_matching_the_published_sha256_is_rejected(tmp_path):
    setup_py = _setup_py(tmp_path)
    responses = _release("9.9.9")
    sha256_url = next(url for url in responses if url.endswith(".sha256"))
    responses[sha256_url] = b"0" * 64 + b"  " + sha256_url.rsplit("/", 1)[-1][: -len(".sha256")].encode()

    with pytest.raises(SystemExit, match="does not match its .sha256 asset"):
        updater.main(["9.9.9"], fetch=responses.__getitem__, setup_py=setup_py)

    assert setup_py.read_text() == SETUP_PY.read_text()


def test_an_archive_missing_from_the_release_is_rejected(tmp_path):
    setup_py = _setup_py(tmp_path)
    responses = _release("9.9.9")
    assets = _release_assets(responses, "9.9.9")
    _set_release_assets(responses, "9.9.9", [a for a in assets if "windows-arm64" not in a["name"]])

    with pytest.raises(SystemExit, match="has no libddwaf-9.9.9-windows-arm64.tar.gz asset"):
        updater.main(["9.9.9"], fetch=responses.__getitem__, setup_py=setup_py)

    assert setup_py.read_text() == SETUP_PY.read_text()


def test_an_archive_without_a_github_digest_is_rejected(tmp_path):
    setup_py = _setup_py(tmp_path)
    responses = _release("9.9.9")
    assets = _release_assets(responses, "9.9.9")
    for asset in assets:
        asset["digest"] = None
    _set_release_assets(responses, "9.9.9", assets)

    with pytest.raises(SystemExit, match="GitHub reports no SHA-256 digest"):
        updater.main(["9.9.9"], fetch=responses.__getitem__, setup_py=setup_py)

    assert setup_py.read_text() == SETUP_PY.read_text()


@pytest.mark.parametrize("version", ["2.2", "2.2.0\nimport os", '2.2.0"', "latest"])
def test_an_invalid_version_is_rejected(tmp_path, version):
    setup_py = _setup_py(tmp_path)

    with pytest.raises(SystemExit):
        updater.main([version], fetch={}.__getitem__, setup_py=setup_py)

    assert setup_py.read_text() == SETUP_PY.read_text()


def test_the_github_token_is_only_sent_to_the_github_api(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "secret")

    api = updater.request(updater.RELEASE_API % "2.2.0")
    asset = updater.request("https://github.com/DataDog/libddwaf/releases/download/2.2.0/x.tar.gz.sha256")

    assert api.unredirected_hdrs == {"Authorization": "Bearer secret"}
    assert "Authorization" not in asset.headers and "Authorization" not in asset.unredirected_hdrs

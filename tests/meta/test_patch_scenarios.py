import os
from pathlib import Path


def test_patch_collection_preserves_methods_and_outcomes(pytester, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    pytester.makeconftest(
        (root / "tests/conftest.py").read_text() + "\nfrom tests.contrib.conftest import pytest_pycollect_makeitem\n"
    )
    pytester.makepyfile(
        test_patch="""
        import os
        from pathlib import Path
        import unittest
        import pytest
        from tests.contrib.patch import PatchTestCase

        class TestPatch(PatchTestCase.Base):
            def __init__(self, *args, **kwargs):
                unittest.TestCase.__init__(self, *args, **kwargs)

            def setUp(self):
                assert os.getpid() != int(os.environ["COLLECTION_PID"])
                assert not Path(os.environ["CLEANUP_DIR"], self._testMethodName).exists()

            def tearDown(self):
                Path(os.environ["CLEANUP_DIR"], self._testMethodName).touch()

            def test_patch_import(self):
                assert os.environ["PATCH_CHILD_SETTING"] == "configured"

            def test_custom_failure(self):
                assert False, "patch assertion must execute"

            def test_custom_skip(self):
                self.skipTest("scenario unavailable")

            @pytest.mark.skip(reason="method unavailable")
            def test_custom_mark_skip(self):
                raise AssertionError("skipped method ran")

            @pytest.mark.xfail(strict=True, reason="known failure")
            def test_custom_xfail(self):
                raise AssertionError("known failure")

        @pytest.mark.skip(reason="class unavailable")
        class TestSkipped(TestPatch):
            pass
    """
    )
    # The parent fixture supplies configuration; unittest setup belongs only to the child.
    conftest = pytester.path / "conftest.py"
    with conftest.open("a") as stream:
        stream.write(
            "\n@pytest.fixture(autouse=True)\n"
            "def patch_environment(monkeypatch):\n"
            "    monkeypatch.setenv('COLLECTION_PID', str(os.getpid()))\n"
            "    monkeypatch.setenv('PATCH_CHILD_SETTING', 'configured')\n"
        )
    monkeypatch.setenv("CLEANUP_DIR", str(pytester.path))
    monkeypatch.setenv("DD_API_KEY", "foobar.baz")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(pytester.path), str(root), os.getenv("PYTHONPATH", "")]))
    result = pytester.runpytest_subprocess(
        "--no-cov",
        "-o",
        "addopts=",
        "-k",
        "custom or (test_patch_import and not test_patch_import_)",
        "test_patch.py",
        timeout=30,
    )
    result.assert_outcomes(passed=1, failed=1, skipped=7, xfailed=1, deselected=42)
    result.stdout.fnmatch_lines(["*patch assertion must execute*"])
    for scenario in ("test_patch_import", "test_custom_failure", "test_custom_skip", "test_custom_xfail"):
        assert (pytester.path / scenario).exists()
    assert not (pytester.path / "test_custom_mark_skip").exists()

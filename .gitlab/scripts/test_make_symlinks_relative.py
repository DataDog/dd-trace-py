#!/usr/bin/env python3
"""Tests for make-symlinks-relative.py.

The reference implementation is the shell loop that prepare-oci-package.sh used
before the Python helper. The equivalence tests need GNU realpath, so they skip
on hosts without `realpath --relative-to` (for example, stock macOS).
"""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


_SCRIPT = Path(__file__).with_name("make-symlinks-relative.py")

_SHELL_LOOP = r"""
set -eo pipefail
find . -type l | while read -r l; do
  target="$(realpath "$l")"
  rel_target="$(realpath --relative-to="$(dirname "$(realpath -s "$l")")" "$target")"
  dest_base="$(basename "$l")"
  dest_dir="$(dirname "$l")"
  (cd "${dest_dir}" && ln -sf "${rel_target}" "${dest_base}")
done
"""


def _gnu_realpath_available():
    try:
        result = subprocess.run(["realpath", "--relative-to=/", "/"], capture_output=True)
    except FileNotFoundError:
        return False
    return result.returncode == 0


def _run_script(root):
    return subprocess.run([sys.executable, str(_SCRIPT), str(root)], capture_output=True, text=True)


def _run_shell_loop(root):
    return subprocess.run(["bash", "-c", _SHELL_LOOP], cwd=root, capture_output=True, text=True)


def _listing(root):
    """Return every entry under `root` with its type, and its link text for symlinks."""
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in sorted(dirnames + filenames):
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            if os.path.islink(path):
                entries.append((rel, "link", os.readlink(path)))
            elif os.path.isdir(path):
                entries.append((rel, "dir", ""))
            else:
                with open(path, "rb") as f:
                    entries.append((rel, "file", f.read().decode("utf-8", "replace")))
    return sorted(entries)


class MakeSymlinksRelativeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def _tree(self, name="sources"):
        """Return a tree shaped like the rdfind output in prepare-oci-package.sh."""
        root = self.tmp / name
        py39 = root / "ddtrace_pkgs" / "site-packages-ddtrace-py3.9-manylinux2014"
        py310 = root / "ddtrace_pkgs" / "site-packages-ddtrace-py3.10-manylinux2014"
        for base in (py39, py310):
            (base / "ddtrace" / "internal").mkdir(parents=True)
            (base / "bytecode-0.17.0.dist-info").mkdir(parents=True)
        (py39 / "ddtrace" / "__init__.py").write_text("init")
        (py39 / "ddtrace" / "internal" / "_utils.cpython-39-x86_64-linux-gnu.so").write_text("so")
        (py39 / "bytecode-0.17.0.dist-info" / "RECORD").write_text("record")
        (py39 / "with space.txt").write_text("space")
        (root / "sitecustomize.py").write_text("sitecustomize")

        abs_py39 = os.path.abspath(py39)
        os.symlink(os.path.join(abs_py39, "ddtrace", "__init__.py"), py310 / "ddtrace" / "__init__.py")
        os.symlink(
            os.path.join(abs_py39, "ddtrace", "internal", "_utils.cpython-39-x86_64-linux-gnu.so"),
            py310 / "ddtrace" / "internal" / "_utils.cpython-39-x86_64-linux-gnu.so",
        )
        os.symlink(
            os.path.join(abs_py39, "bytecode-0.17.0.dist-info", "RECORD"),
            py310 / "bytecode-0.17.0.dist-info" / "RECORD",
        )
        os.symlink(os.path.join(abs_py39, "with space.txt"), py310 / "with space.txt")
        # A chain: the second link points at the first link, not at the file.
        os.symlink(os.path.join(os.path.abspath(py310), "ddtrace", "__init__.py"), py310 / "chain.py")
        # A link at the top of the tree that points into a subdirectory.
        os.symlink(os.path.join(abs_py39, "ddtrace", "__init__.py"), root / "top.py")
        return root

    def test_rewrites_absolute_links_as_relative(self):
        root = self._tree()
        result = _run_script(root)
        self.assertEqual(result.returncode, 0, result.stderr)

        links = {rel: target for rel, kind, target in _listing(root) if kind == "link"}
        py310 = "ddtrace_pkgs/site-packages-ddtrace-py3.10-manylinux2014"
        up = "../../site-packages-ddtrace-py3.9-manylinux2014"
        self.assertEqual(
            links,
            {
                py310 + "/ddtrace/__init__.py": up + "/ddtrace/__init__.py",
                py310 + "/ddtrace/internal/_utils.cpython-39-x86_64-linux-gnu.so": (
                    "../" + up + "/ddtrace/internal/_utils.cpython-39-x86_64-linux-gnu.so"
                ),
                py310 + "/bytecode-0.17.0.dist-info/RECORD": up + "/bytecode-0.17.0.dist-info/RECORD",
                py310 + "/with space.txt": "../site-packages-ddtrace-py3.9-manylinux2014/with space.txt",
                py310 + "/chain.py": "../site-packages-ddtrace-py3.9-manylinux2014/ddtrace/__init__.py",
                "top.py": "ddtrace_pkgs/site-packages-ddtrace-py3.9-manylinux2014/ddtrace/__init__.py",
            },
        )
        for rel in links:
            self.assertTrue((root / rel).exists(), rel)

    def test_is_idempotent(self):
        root = self._tree()
        self.assertEqual(_run_script(root).returncode, 0)
        first = _listing(root)
        result = _run_script(root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(_listing(root), first)

    def test_handles_names_that_the_shell_loop_mangles(self):
        root = self.tmp / "sources"
        (root / "a").mkdir(parents=True)
        (root / "b").mkdir()
        for name in ("trailing space ", "back\\slash", "new\nline"):
            (root / "a" / name).write_text(name)
            os.symlink(os.path.abspath(root / "a" / name), root / "b" / name)

        result = _run_script(root)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("trailing space ", "back\\slash", "new\nline"):
            self.assertEqual(os.readlink(root / "b" / name), "../a/" + name)
        self.assertEqual(sorted(os.listdir(root / "b")), sorted(["trailing space ", "back\\slash", "new\nline"]))

    def test_broken_link_fails_without_changes(self):
        root = self._tree()
        os.symlink(os.path.abspath(root / "missing.py"), root / "broken.py")
        before = _listing(root)
        result = _run_script(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("broken.py", result.stderr)
        self.assertEqual(_listing(root), before)

    def test_link_to_directory_fails_without_changes(self):
        root = self._tree()
        os.symlink(os.path.abspath(root / "ddtrace_pkgs"), root / "pkgs")
        before = _listing(root)
        result = _run_script(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pkgs", result.stderr)
        self.assertEqual(_listing(root), before)

    @unittest.skipUnless(_gnu_realpath_available(), "needs GNU realpath")
    def test_matches_shell_loop(self):
        expected = self._tree("expected")
        actual = self._tree("actual")
        result = _run_shell_loop(expected)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = _run_script(actual)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(_listing(actual), _listing(expected))

    @unittest.skipUnless(_gnu_realpath_available(), "needs GNU realpath")
    def test_matches_shell_loop_when_root_path_has_a_symlink(self):
        # The shell loop resolves symlinks in the directory of each link, so the
        # relative path must not depend on how the caller spells the root.
        expected = self._tree("expected")
        actual = self._tree("actual")
        os.symlink(self.tmp, self.tmp / "alias")
        result = _run_shell_loop(self.tmp / "alias" / "expected")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = _run_script(self.tmp / "alias" / "actual")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(_listing(actual), _listing(expected))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python
"""Vendored virtualenv-clone utility for cloning Python virtual environments.

This module is vendored from virtualenv-clone v0.5.7 (https://github.com/edwardgeorge/virtualenv-clone).

Original Copyright Notice:
    Copyright (c) 2011 Edward George
    Based on code from the virtualenv project.

Original License: MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

Vendorization Notes:
    - This code was vendored to avoid external dependencies for IAST testing.
    - The original project has not received updates since 2021.
    - This version has been modernized for Python 3.9+ only (Python 2.7-3.8 support removed).
    - Type hints and additional documentation have been added.

Modifications from original:
    - Dropped support for Python 2.7, 3.5, 3.6, 3.7, and 3.8
    - Added type hints throughout
    - Added comprehensive docstrings
    - Modernized code for Python 3.9+
    - Removed legacy compatibility code
"""

import itertools
import logging
import optparse
import os
import os.path
import re
import shutil
import subprocess
import sys
from typing import List
from typing import Optional
from typing import Tuple


__version__ = "0.5.7"


logger = logging.getLogger()


env_bin_dir = "bin"
if sys.platform == "win32":
    env_bin_dir = "Scripts"


class UserError(Exception):
    """Exception raised for user-facing errors in virtualenv cloning operations."""

    pass


def _dirmatch(path: str, matchwith: str) -> bool:
    """Check if a path is within another path's directory tree.

    Args:
        path: The path to check.
        matchwith: The parent directory path to match against.

    Returns:
        True if path is within matchwith's tree, False otherwise.

    Examples:
        >>> _dirmatch('/home/foo/bar', '/home/foo/bar')
        True
        >>> _dirmatch('/home/foo/bar/', '/home/foo/bar')
        True
        >>> _dirmatch('/home/foo/bar/etc', '/home/foo/bar')
        True
        >>> _dirmatch('/home/foo/bar2', '/home/foo/bar')
        False
        >>> _dirmatch('/home/foo/bar2/etc', '/home/foo/bar')
        False
    """
    matchlen = len(matchwith)
    if path.startswith(matchwith) and path[matchlen : matchlen + 1] in [os.sep, ""]:
        return True
    return False


def _virtualenv_sys(venv_path: str) -> Tuple[str, List[str]]:
    """Obtain Python version and sys.path information from a virtualenv.

    Args:
        venv_path: Path to the virtual environment.

    Returns:
        A tuple containing:
            - Python version as a string (e.g., "3.9")
            - List of sys.path entries (excluding empty strings)

    Raises:
        AssertionError: If the subprocess fails or returns no output.
    """
    executable = os.path.join(venv_path, env_bin_dir, "python")
    # Must use "executable" as the first argument rather than as the
    # keyword argument "executable" to get correct value from sys.path
    python_code = (
        'import sys;print ("%d.%d" % (sys.version_info.major, sys.version_info.minor));print ("\\n".join(sys.path));'
    )
    p = subprocess.Popen(
        [executable, "-c", python_code],
        env={},
        stdout=subprocess.PIPE,
    )
    stdout, err = p.communicate()
    assert not p.returncode and stdout
    lines = stdout.decode("utf-8").splitlines()
    return lines[0], list(filter(bool, lines[1:]))


def clone_virtualenv(src_dir: str, dst_dir: str) -> None:
    """Clone a virtual environment from source to destination directory.

    This function performs a complete clone of a virtual environment, including:
    - Copying all files and directories (preserving symlinks)
    - Fixing script shebangs to point to the new location
    - Updating sys.path references in .pth and .egg-link files
    - Fixing internal symlinks that point to the original venv

    Args:
        src_dir: Path to the source virtual environment to clone.
        dst_dir: Path where the cloned virtual environment will be created.

    Raises:
        UserError: If src_dir doesn't exist or dst_dir already exists.
        AssertionError: If old paths remain in sys.path after fixup.
    """
    if not os.path.exists(src_dir):
        raise UserError("src dir %r does not exist" % src_dir)
    if os.path.exists(dst_dir):
        raise UserError("dest dir %r exists" % dst_dir)

    logger.info("cloning virtualenv '%s' => '%s'...", src_dir, dst_dir)
    shutil.copytree(src_dir, dst_dir, symlinks=True, ignore=shutil.ignore_patterns("*.pyc"))
    version, sys_path = _virtualenv_sys(dst_dir)
    logger.info("fixing scripts in bin...")
    fixup_scripts(src_dir, dst_dir, version)

    def has_old(s: List[str]) -> bool:
        return any(i for i in s if _dirmatch(i, src_dir))

    if has_old(sys_path):
        # only need to fix stuff in sys.path if we have old
        # paths in the sys.path of new python env. right?
        logger.info("fixing paths in sys.path...")
        fixup_syspath_items(sys_path, src_dir, dst_dir)
    v_sys = _virtualenv_sys(dst_dir)
    remaining = has_old(v_sys[1])
    assert not remaining, v_sys
    fix_symlink_if_necessary(src_dir, dst_dir)


def fix_symlink_if_necessary(src_dir: str, dst_dir: str) -> None:
    """Fix internal symlinks in the cloned virtualenv that point to the original location.

    Sometimes the source virtual environment has symlinks that point to itself.
    For example, $OLD_VIRTUAL_ENV/local/lib might point to $OLD_VIRTUAL_ENV/lib.
    This function ensures that $NEW_VIRTUAL_ENV/local/lib will point to $NEW_VIRTUAL_ENV/lib.

    Note:
        This issue usually goes unnoticed unless one tries to upgrade a package via pip,
        making it a subtle but important bug to fix.

    Args:
        src_dir: Path to the source virtual environment.
        dst_dir: Path to the destination virtual environment.
    """
    logger.info("scanning for internal symlinks that point to the original virtual env")
    for dirpath, dirnames, filenames in os.walk(dst_dir):
        for a_file in itertools.chain(filenames, dirnames):
            full_file_path = os.path.join(dirpath, a_file)
            if os.path.islink(full_file_path):
                target = os.path.realpath(full_file_path)
                if target.startswith(src_dir):
                    new_target = target.replace(src_dir, dst_dir)
                    logger.debug("fixing symlink in %s", full_file_path)
                    os.remove(full_file_path)
                    os.symlink(new_target, full_file_path)


def fixup_scripts(old_dir: str, new_dir: str, version: str, rewrite_env_python: bool = False) -> None:
    """Fix script shebangs and activation scripts in the cloned virtualenv's bin directory.

    Args:
        old_dir: Path to the original virtual environment.
        new_dir: Path to the cloned virtual environment.
        version: Python version string (e.g., "3.9").
        rewrite_env_python: Whether to rewrite shebangs that use "#!/usr/bin/env python".
                           Default is False.
    """
    bin_dir = os.path.join(new_dir, env_bin_dir)
    root, dirs, files = next(os.walk(bin_dir))
    pybinre = re.compile(r"pythonw?([0-9]+(\.[0-9]+(\.[0-9]+)?)?)?$")
    for file_ in files:
        filename = os.path.join(root, file_)
        if file_ in ["python", "python%s" % version, "activate_this.py"]:
            continue
        elif file_.startswith("python") and pybinre.match(file_):
            # ignore other possible python binaries
            continue
        elif file_.endswith(".pyc"):
            # ignore compiled files
            continue
        elif file_ == "activate" or file_.startswith("activate."):
            fixup_activate(os.path.join(root, file_), old_dir, new_dir)
        elif os.path.islink(filename):
            fixup_link(filename, old_dir, new_dir)
        elif os.path.isfile(filename):
            fixup_script_(root, file_, old_dir, new_dir, version, rewrite_env_python=rewrite_env_python)


def fixup_script_(
    root: str, file_: str, old_dir: str, new_dir: str, version: str, rewrite_env_python: bool = False
) -> None:
    """Fix shebang lines in a single script file.

    This function rewrites shebang lines that reference the old virtualenv path
    to use the new virtualenv path instead.

    Args:
        root: Directory containing the script.
        file_: Name of the script file.
        old_dir: Path to the original virtual environment.
        new_dir: Path to the cloned virtual environment.
        version: Python version string (e.g., "3.9").
        rewrite_env_python: Whether to rewrite shebangs that use "#!/usr/bin/env python".
    """
    old_shebang = "#!%s/bin/python" % os.path.normcase(os.path.abspath(old_dir))
    new_shebang = "#!%s/bin/python" % os.path.normcase(os.path.abspath(new_dir))
    env_shebang = "#!/usr/bin/env python"

    filename = os.path.join(root, file_)
    with open(filename, "rb") as f:
        if f.read(2) != b"#!":
            # no shebang
            return
        f.seek(0)
        lines = f.readlines()

    if not lines:
        # warn: empty script
        return

    def rewrite_shebang(version: Optional[str] = None) -> None:
        """Rewrite the shebang line in the current script.

        Args:
            version: Optional Python version to append to shebang (e.g., "3.9").
        """
        logger.debug("fixing %s", filename)
        shebang = new_shebang
        if version:
            shebang = shebang + version
        shebang = (shebang + "\n").encode("utf-8")
        with open(filename, "wb") as f:
            f.write(shebang)
            f.writelines(lines[1:])

    try:
        bang = lines[0].decode("utf-8").strip()
    except UnicodeDecodeError:
        # binary file
        return

    # This takes care of the scheme in which shebang is of type
    # '#!/venv/bin/python3' while the version of system python
    # is of type 3.x e.g. 3.5.
    short_version = bang[len(old_shebang) :]

    if not bang.startswith("#!"):
        return
    elif bang == old_shebang:
        rewrite_shebang()
    elif bang.startswith(old_shebang) and bang[len(old_shebang) :] == version:
        rewrite_shebang(version)
    elif bang.startswith(old_shebang) and short_version and bang[len(old_shebang) :] == short_version:
        rewrite_shebang(short_version)
    elif rewrite_env_python and bang.startswith(env_shebang):
        if bang == env_shebang:
            rewrite_shebang()
        elif bang[len(env_shebang) :] == version:
            rewrite_shebang(version)
    else:
        # can't do anything
        return


def fixup_activate(filename: str, old_dir: str, new_dir: str) -> None:
    """Fix path references in virtualenv activation scripts.

    Args:
        filename: Path to the activation script.
        old_dir: Path to the original virtual environment.
        new_dir: Path to the cloned virtual environment.
    """
    logger.debug("fixing %s", filename)
    with open(filename, "rb") as f:
        data = f.read().decode("utf-8")

    data = data.replace(old_dir, new_dir)
    with open(filename, "wb") as f:
        f.write(data.encode("utf-8"))


def fixup_link(filename: str, old_dir: str, new_dir: str, target: Optional[str] = None) -> None:
    """Fix a symbolic link to point to the new virtualenv location if necessary.

    Args:
        filename: Path to the symbolic link.
        old_dir: Path to the original virtual environment.
        new_dir: Path to the cloned virtual environment.
        target: Optional explicit target path (if None, will be read from the link).
    """
    logger.debug("fixing %s", filename)
    if target is None:
        target = os.readlink(filename)

    origdir = os.path.dirname(os.path.abspath(filename)).replace(new_dir, old_dir)
    if not os.path.isabs(target):
        target = os.path.abspath(os.path.join(origdir, target))
        rellink = True
    else:
        rellink = False

    if _dirmatch(target, old_dir):
        if rellink:
            # keep relative links, but don't keep original in case it
            # traversed up out of, then back into the venv.
            # so, recreate a relative link from absolute.
            target = target[len(origdir) :].lstrip(os.sep)
        else:
            target = target.replace(old_dir, new_dir, 1)

    # else: links outside the venv, replaced with absolute path to target.
    _replace_symlink(filename, target)


def _replace_symlink(filename: str, newtarget: str) -> None:
    """Atomically replace a symbolic link with a new target.

    Args:
        filename: Path to the symbolic link to replace.
        newtarget: New target path for the symbolic link.
    """
    tmpfn = "%s.new" % filename
    os.symlink(newtarget, tmpfn)
    os.rename(tmpfn, filename)


def fixup_syspath_items(syspath: List[str], old_dir: str, new_dir: str) -> None:
    """Fix path references in .pth and .egg-link files within sys.path directories.

    Args:
        syspath: List of sys.path entries to process.
        old_dir: Path to the original virtual environment.
        new_dir: Path to the cloned virtual environment.
    """
    for path in syspath:
        if not os.path.isdir(path):
            continue
        path = os.path.normcase(os.path.abspath(path))
        if _dirmatch(path, old_dir):
            path = path.replace(old_dir, new_dir, 1)
            if not os.path.exists(path):
                continue
        elif not _dirmatch(path, new_dir):
            continue
        root, dirs, files = next(os.walk(path))
        for file_ in files:
            filename = os.path.join(root, file_)
            if filename.endswith(".pth"):
                fixup_pth_file(filename, old_dir, new_dir)
            elif filename.endswith(".egg-link"):
                fixup_egglink_file(filename, old_dir, new_dir)


def fixup_pth_file(filename: str, old_dir: str, new_dir: str) -> None:
    """Fix path references in a .pth file.

    .pth files contain additional paths to add to sys.path. This function
    updates any paths that reference the old virtualenv to use the new one.

    Args:
        filename: Path to the .pth file.
        old_dir: Path to the original virtual environment.
        new_dir: Path to the cloned virtual environment.
    """
    logger.debug("fixup_pth_file %s", filename)

    with open(filename, "r") as f:
        lines = f.readlines()

    has_change = False

    for num, line in enumerate(lines):
        line = line.strip()

        if not line or line.startswith("#") or line.startswith("import "):
            continue
        elif _dirmatch(line, old_dir):
            lines[num] = line.replace(old_dir, new_dir, 1)
            has_change = True

    if has_change:
        with open(filename, "w") as f:
            payload = os.linesep.join([line.strip() for line in lines]) + os.linesep
            f.write(payload)


def fixup_egglink_file(filename: str, old_dir: str, new_dir: str) -> None:
    """Fix path references in an .egg-link file.

    .egg-link files contain paths to editable package installations.
    This function updates the path if it references the old virtualenv.

    Args:
        filename: Path to the .egg-link file.
        old_dir: Path to the original virtual environment.
        new_dir: Path to the cloned virtual environment.
    """
    logger.debug("fixing %s", filename)
    with open(filename, "rb") as f:
        link = f.read().decode("utf-8").strip()
    if _dirmatch(link, old_dir):
        link = link.replace(old_dir, new_dir, 1)
        with open(filename, "wb") as f:
            link = (link + "\n").encode("utf-8")
            f.write(link)


def main() -> None:
    """Main entry point for the virtualenv-clone command-line tool."""
    parser = optparse.OptionParser("usage: %prog [options] /path/to/existing/venv /path/to/cloned/venv")
    parser.add_option("-v", action="count", dest="verbose", default=False, help="verbosity")
    options, args = parser.parse_args()
    try:
        old_dir, new_dir = args
    except ValueError:
        print("virtualenv-clone %s" % (__version__,))
        parser.error("not enough arguments given.")
    old_dir = os.path.realpath(old_dir)
    new_dir = os.path.realpath(new_dir)
    loglevel = (logging.WARNING, logging.INFO, logging.DEBUG)[min(2, options.verbose)]
    logging.basicConfig(level=loglevel, format="%(message)s")
    try:
        clone_virtualenv(old_dir, new_dir)
    except UserError:
        e = sys.exc_info()[1]
        parser.error(str(e))


if __name__ == "__main__":
    main()

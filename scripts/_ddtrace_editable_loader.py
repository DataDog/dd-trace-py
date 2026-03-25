"""Shadow _ddtrace_editable_loader.py — prevents subprocess from leaking into sys.modules.

When meson-python's editable install is used, ``pip install -e .`` generates
``_ddtrace_editable_loader.py`` in the venv's site-packages and installs a
``ddtrace-editable.pth`` file whose sole content is::

    import _ddtrace_editable_loader

The generated loader imports ``subprocess`` at module level (even though
``subprocess`` is only used inside ``_rebuild()``, which our sitecustomize.py
patches away).  This causes ``subprocess`` to appear in ``sys.modules`` at
Python startup — breaking ``test_auto``, which asserts that
``import ddtrace.auto`` does not transitively import ``subprocess``.

Because ``scripts/`` is in ``PYTHONPATH`` (added by docker-compose.yml and by
the ``conftest.py`` subprocess wrapper), this file appears *earlier* in
``sys.path`` than the venv's site-packages.  When ``ddtrace-editable.pth``
executes ``import _ddtrace_editable_loader``, Python finds this shadow first.

What this shadow does:
1. Finds the *real* ``_ddtrace_editable_loader.py`` further along ``sys.path``
   (in the base riot venv's site-packages).
2. ``exec``s the real file in this module's globals namespace so that every
   name it defines (classes, ``install()``, ``collect()``, …) is available in
   this module — and so the ``install()`` call at the bottom registers the
   ``MesonpyMetaFinder`` in ``sys.meta_path`` exactly as it would normally.
3. Removes ``subprocess`` from ``sys.modules`` afterwards if it was not
   already present before this import.  The ``MesonpyMetaFinder`` object
   retains a live reference to the ``subprocess`` module object through its
   own globals, so ``_rebuild()`` still works (and sitecustomize.py patches
   it to avoid calling ninja anyway).

This is a pure no-op in environments where ``subprocess`` was already present
(e.g., when code that uses subprocess imports ddtrace later).
"""
import os as _os
import sys as _sys


def _exec_real_loader():
    # Snapshot whether subprocess was already imported.
    _sub_was_present = "subprocess" in _sys.modules

    # Locate the real loader in site-packages (skip this file's own directory).
    _this_dir = _os.path.dirname(_os.path.abspath(__file__))
    _real_path = None
    for _p in _sys.path:
        _p = _os.path.abspath(_p) if _p else _os.getcwd()
        if _p == _this_dir:
            continue
        _candidate = _os.path.join(_p, "_ddtrace_editable_loader.py")
        if _os.path.isfile(_candidate):
            _real_path = _candidate
            break

    if _real_path is None:
        # No generated loader found — nothing to do.
        return

    try:
        with open(_real_path, "r", encoding="utf-8") as _f:
            _src = _f.read()
        # Execute in *this* module's globals so all definitions (collect,
        # MesonpyMetaFinder, install, …) become attributes of this module and
        # install() registers the finder in sys.meta_path.
        exec(compile(_src, _real_path, "exec"), globals())  # noqa: S102
    except Exception:
        # If loading fails, leave everything as-is; errors will surface naturally.
        return

    # Remove subprocess from sys.modules if we are the ones who caused it to
    # appear.  The MetaFinder holds a direct reference to the module object
    # through its globals, so it continues to work after this removal.
    if not _sub_was_present:
        _sys.modules.pop("subprocess", None)


_exec_real_loader()
del _exec_real_loader

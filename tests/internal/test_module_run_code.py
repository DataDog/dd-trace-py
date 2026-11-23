import runpy

import pytest

from ddtrace.internal.module import register_post_run_module_hook
from ddtrace.internal.module import unregister_post_run_module_hook


# DEV: kept apart from test_module.py, whose type check follows an import of a deliberately broken test fixture.


def test_post_run_module_hook_module_not_in_sys_modules(tmp_path, monkeypatch):
    # runpy.run_module only registers the module in sys.modules when alter_sys is true, so the hook must not look
    # it up unconditionally (a KeyError would also mask any error raised by the module itself).
    (tmp_path / "dd_run_code_target.py").write_text("answer = 42\n")
    (tmp_path / "dd_run_code_failing.py").write_text("raise ValueError('original')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    seen = []

    register_post_run_module_hook(seen.append)
    try:
        assert runpy.run_module("dd_run_code_target", run_name="not_in_sys_modules")["answer"] == 42
        with pytest.raises(ValueError, match="original"):
            runpy.run_module("dd_run_code_failing", run_name="not_in_sys_modules")
        assert seen == []

        runpy.run_module("dd_run_code_target", run_name="in_sys_modules", alter_sys=True)
        assert [m.__name__ for m in seen] == ["in_sys_modules"]
    finally:
        unregister_post_run_module_hook(seen.append)

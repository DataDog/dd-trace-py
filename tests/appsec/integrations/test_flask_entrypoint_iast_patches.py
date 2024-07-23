import pytest


@pytest.mark.subprocess()
def test_ddtrace_iast_flask_patch():
    import dis
    import io
    import sys

    from tests.utils import override_env
    from tests.utils import override_global_config

    with override_global_config(dict(_iast_enabled=True)), override_env(
        dict(DD_IAST_ENABLED="true", DD_IAST_REQUEST_SAMPLING="100")
    ):
        import tests.appsec.iast.fixtures.entrypoint.app_main_patched as flask_entrypoint

        dis_output = io.StringIO()
        dis.dis(flask_entrypoint, file=dis_output)
        str_output = dis_output.getvalue()
        # Should have replaced the binary op with the aspect in add_test:
        assert "(add_aspect)" in str_output
        assert "BINARY_ADD" in str_output or "BINARY_OP" not in str_output
        # Should have replaced the app.run() with a pass:
        assert "Disassembly of run" not in str_output
        del sys.modules["tests.appsec.iast.fixtures.entrypoint.app_main_patched"]


@pytest.mark.subprocess()
def test_ddtrace_iast_flask_patch_iast_disabled():
    import dis
    import io
    import sys

    from ddtrace import ModuleWatchdog
    from tests.utils import override_env
    from tests.utils import override_global_config

    def _uninstall_watchdog_and_reload():
        if len(ModuleWatchdog._instance._pre_exec_module_hooks) > 0:
            ModuleWatchdog._instance._pre_exec_module_hooks.pop()
        assert ModuleWatchdog._instance._pre_exec_module_hooks == set()

    _uninstall_watchdog_and_reload()
    with override_global_config(dict(_iast_enabled=False)), override_env(dict(DD_IAST_ENABLED="false")):
        import tests.appsec.iast.fixtures.entrypoint.app_main_patched as flask_entrypoint

        dis_output = io.StringIO()
        dis.dis(flask_entrypoint, file=dis_output)
        str_output = dis_output.getvalue()
        # Should have replaced the binary op with the aspect in add_test:
        assert "(add_aspect)" not in str_output
        del sys.modules["tests.appsec.iast.fixtures.entrypoint.app_main_patched"]


@pytest.mark.subprocess()
def test_ddtrace_iast_flask_no_patch():
    import dis
    import io
    import sys

    from ddtrace import ModuleWatchdog
    from tests.utils import override_env
    from tests.utils import override_global_config

    def _uninstall_watchdog_and_reload():
        if len(ModuleWatchdog._instance._pre_exec_module_hooks) > 0:
            ModuleWatchdog._instance._pre_exec_module_hooks.pop()
        assert ModuleWatchdog._instance._pre_exec_module_hooks == set()

    _uninstall_watchdog_and_reload()
    with override_global_config(dict(_iast_enabled=True)), override_env(
        dict(DD_IAST_ENABLED="true", DD_IAST_REQUEST_SAMPLING="100")
    ):
        import tests.appsec.iast.fixtures.entrypoint.app as flask_entrypoint

        dis_output = io.StringIO()
        dis.dis(flask_entrypoint, file=dis_output)
        str_output = dis_output.getvalue()
        # Should have replaced the binary op with the aspect in add_test:
        assert "(add_aspect)" not in str_output
        # Should have replaced the app.run() with a pass:
        assert "Disassembly of run" in str_output
        del sys.modules["tests.appsec.iast.fixtures.entrypoint.app"]


@pytest.mark.subprocess()
def test_ddtrace_iast_flask_app_create_app_enable_iast_propagation():
    import dis
    import io
    import sys

    from ddtrace import ModuleWatchdog
    from tests.utils import override_env
    from tests.utils import override_global_config

    def _uninstall_watchdog_and_reload():
        if len(ModuleWatchdog._instance._pre_exec_module_hooks) > 0:
            ModuleWatchdog._instance._pre_exec_module_hooks.pop()
        assert ModuleWatchdog._instance._pre_exec_module_hooks == set()

    _uninstall_watchdog_and_reload()
    with override_global_config(dict(_iast_enabled=True)), override_env(
        dict(DD_IAST_ENABLED="true", DD_IAST_REQUEST_SAMPLING="100")
    ):
        import tests.appsec.iast.fixtures.entrypoint.app_create_app_patch_all  # noqa: F401
        import tests.appsec.iast.fixtures.entrypoint.views as flask_entrypoint_views

        dis_output = io.StringIO()
        dis.dis(flask_entrypoint_views, file=dis_output)
        str_output = dis_output.getvalue()
        # Should have replaced the binary op with the aspect in add_test:
        assert "(add_aspect)" not in str_output
        assert "BINARY_ADD" in str_output or "BINARY_OP" in str_output
        del sys.modules["tests.appsec.iast.fixtures.entrypoint.app_create_app_patch_all"]
        del sys.modules["tests.appsec.iast.fixtures.entrypoint.views"]


@pytest.mark.subprocess()
def test_ddtrace_iast_flask_app_create_app_patch_all():
    import dis
    import io
    import sys

    from ddtrace import ModuleWatchdog
    from tests.utils import override_env
    from tests.utils import override_global_config

    def _uninstall_watchdog_and_reload():
        if len(ModuleWatchdog._instance._pre_exec_module_hooks) > 0:
            ModuleWatchdog._instance._pre_exec_module_hooks.pop()
        assert ModuleWatchdog._instance._pre_exec_module_hooks == set()

    _uninstall_watchdog_and_reload()
    with override_global_config(dict(_iast_enabled=True)), override_env(dict(DD_IAST_ENABLED="true")):
        import tests.appsec.iast.fixtures.entrypoint.app_create_app_patch_all  # noqa: F401
        import tests.appsec.iast.fixtures.entrypoint.views as flask_entrypoint_views

        dis_output = io.StringIO()
        dis.dis(flask_entrypoint_views, file=dis_output)
        str_output = dis_output.getvalue()
        # Should have replaced the binary op with the aspect in add_test:
        assert "(add_aspect)" not in str_output
        assert "BINARY_ADD" in str_output or "BINARY_OP" in str_output
        del sys.modules["tests.appsec.iast.fixtures.entrypoint.app_create_app_patch_all"]
        del sys.modules["tests.appsec.iast.fixtures.entrypoint.views"]


@pytest.mark.subprocess()
def test_ddtrace_iast_flask_app_create_app_patch_all_enable_iast_propagation():
    import dis
    import io
    import sys

    from ddtrace import ModuleWatchdog
    from tests.utils import override_env
    from tests.utils import override_global_config

    def _uninstall_watchdog_and_reload():
        if len(ModuleWatchdog._instance._pre_exec_module_hooks) > 0:
            ModuleWatchdog._instance._pre_exec_module_hooks.pop()
        assert ModuleWatchdog._instance._pre_exec_module_hooks == set()

    _uninstall_watchdog_and_reload()
    with override_global_config(dict(_iast_enabled=True)), override_env(dict(DD_IAST_ENABLED="true")):
        import tests.appsec.iast.fixtures.entrypoint.app_create_app_patch_all_enable_iast_propagation  # noqa: F401
        import tests.appsec.iast.fixtures.entrypoint.views as flask_entrypoint_views

        dis_output = io.StringIO()
        dis.dis(flask_entrypoint_views, file=dis_output)
        str_output = dis_output.getvalue()
        # Should have replaced the binary op with the aspect in add_test:
        assert "(add_aspect)" in str_output
        assert "BINARY_ADD" not in str_output or "BINARY_OP" not in str_output
        assert flask_entrypoint_views.add_test() != []
        del sys.modules["tests.appsec.iast.fixtures.entrypoint.app_create_app_patch_all_enable_iast_propagation"]
        del sys.modules["tests.appsec.iast.fixtures.entrypoint.views"]


@pytest.mark.subprocess()
def test_ddtrace_iast_flask_app_create_app_patch_all_enable_iast_propagation_disabled():
    import dis
    import io

    from ddtrace import ModuleWatchdog
    from tests.utils import override_env
    from tests.utils import override_global_config

    def _uninstall_watchdog_and_reload():
        if len(ModuleWatchdog._instance._pre_exec_module_hooks) > 0:
            ModuleWatchdog._instance._pre_exec_module_hooks.pop()
        assert ModuleWatchdog._instance._pre_exec_module_hooks == set()

    _uninstall_watchdog_and_reload()
    with override_global_config(dict(_iast_enabled=False)), override_env(dict(DD_IAST_ENABLED="false")):
        import tests.appsec.iast.fixtures.entrypoint.app_create_app_patch_all_enable_iast_propagation  # noqa: F401
        import tests.appsec.iast.fixtures.entrypoint.views as flask_entrypoint_views

        dis_output = io.StringIO()
        dis.dis(flask_entrypoint_views, file=dis_output)
        str_output = dis_output.getvalue()
        # Should have replaced the binary op with the aspect in add_test:
        assert "(add_aspect)" not in str_output
        assert "BINARY_ADD" in str_output or "BINARY_OP" in str_output

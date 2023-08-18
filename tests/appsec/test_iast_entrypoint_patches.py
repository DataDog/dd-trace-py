import dis
import io

import mock

from tests.utils import override_global_config


def test_ddtrace_iast_flask_patch():
    with override_global_config(dict(_iast_enabled=True)), mock.patch(
        "ddtrace.appsec.iast._util._is_iast_enabled", return_value=True
    ):
        import tests.appsec.iast.fixtures.app as flask_entrypoint

        dis_output = io.StringIO()
        dis.dis(flask_entrypoint, file=dis_output)
        str_output = dis_output.getvalue()
        # Should have replaced the binary op with the aspect in add_test:
        assert "(add_aspect)" in str_output
        assert "BINARY_OP" not in str_output
        # Should have replaced the app.run() with a pass:
        assert "Disassembly of run" not in str_output

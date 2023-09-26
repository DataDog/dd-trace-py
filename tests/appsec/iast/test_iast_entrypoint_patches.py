import dis
import io
import sys

import pytest

from tests.utils import override_env
from tests.utils import override_global_config


@pytest.mark.skipif(sys.version_info[:2] < (3, 7), reason="dis is different in python <= 3.6")
@pytest.mark.skipif(sys.version_info[:2] > (3, 11), reason="IAST is not supported in Pys later than 3.11")
def test_ddtrace_iast_flask_patch():
    with override_global_config(dict(_iast_enabled=True)), override_env(dict(DD_IAST_ENABLED="true")):
        import tests.appsec.iast.fixtures.entrypoint.app_patched as flask_entrypoint

        dis_output = io.StringIO()
        dis.dis(flask_entrypoint, file=dis_output)
        str_output = dis_output.getvalue()
        # Should have replaced the binary op with the aspect in add_test:
        assert "(add_aspect)" in str_output
        assert "BINARY_OP" not in str_output
        # Should have replaced the app.run() with a pass:
        assert "Disassembly of run" not in str_output


@pytest.mark.skipif(sys.version_info[:2] < (3, 7), reason="dis is different in python <= 3.6")
def test_ddtrace_iast_flask_no_patch():
    with override_global_config(dict(_iast_enabled=True)), override_env(dict(DD_IAST_ENABLED="true")):
        import tests.appsec.iast.fixtures.entrypoint.app as flask_entrypoint

        dis_output = io.StringIO()
        dis.dis(flask_entrypoint, file=dis_output)
        str_output = dis_output.getvalue()
        # Should have replaced the binary op with the aspect in add_test:
        assert "(add_aspect)" not in str_output
        # Should have replaced the app.run() with a pass:
        assert "Disassembly of run" in str_output

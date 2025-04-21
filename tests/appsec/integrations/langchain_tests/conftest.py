from ddtrace.appsec._iast import enable_iast_propagation
from ddtrace.appsec._iast._patch_modules import patch_iast
from ddtrace.contrib.internal.langchain.patch import patch as langchain_patch
from tests.utils import override_env
from tests.utils import override_global_config


# `pytest` automatically calls this function once when tests are run.
def pytest_configure():
    with override_global_config(
        dict(
            _iast_enabled=True,
            _iast_deduplication_enabled=False,
            _iast_request_sampling=100.0,
        )
    ), override_env(dict(_DD_IAST_PATCH_MODULES="tests.appsec.integrations")):
        patch_iast()
        enable_iast_propagation()
        langchain_patch()

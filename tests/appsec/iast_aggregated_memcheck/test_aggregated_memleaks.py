import pytest

from tests.utils import override_env
from tests.utils import override_global_config


@pytest.mark.asyncio
async def test_aggregated_leaks():
    env = {"DD_IAST_ENABLED": "true", "DD_IAST_REQUEST_SAMPLING": "100"}
    with override_env(env), override_global_config(dict(_iast_enabled=True, _deduplication_enabled=False)):
        from scripts.iast.leak_functions import iast_leaks

        result = await iast_leaks(75000, 1.0, 100) == 0
        assert result

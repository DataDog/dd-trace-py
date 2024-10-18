import pytest

from tests.utils import override_global_config


@pytest.mark.asyncio
async def test_aggregated_leaks():
    with override_global_config(dict(_iast_enabled=True, _deduplication_enabled=False, _iast_request_sampling=100.0)):
        from scripts.iast.leak_functions import iast_leaks

        result = await iast_leaks(75000, 1.0, 100) == 0
        assert result

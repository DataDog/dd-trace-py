import pytest

from tests.utils import override_global_config


@pytest.mark.asyncio
async def test_aggregated_leaks():
    with override_global_config(
        dict(_iast_enabled=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        from scripts.iast.leak_functions import iast_leaks

        result = await iast_leaks(60000, 0.2, 500) == 0
        assert result

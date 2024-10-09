import pytest

from tests.utils import override_env


@pytest.mark.asyncio
async def test_aggregated_leaks():
    with override_env({"DD_IAST_ENABLED": "true"}):
        from scripts.iast.leak_functions import iast_leaks

        result = await iast_leaks(75000, 1.0, 100) == 0
        assert result

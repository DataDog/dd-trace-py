import pytest


@pytest.mark.snapshot()
async def test_query(claude_agent_sdk, mock_internal_client):
    async for _ in claude_agent_sdk.query(prompt="Hello, world!"):
        pass


@pytest.mark.snapshot(ignores=["meta.error.stack"])
async def test_query_error(claude_agent_sdk, mock_internal_client_error):
    with pytest.raises(ValueError):
        async for _ in claude_agent_sdk.query(prompt="This will fail"):
            pass


@pytest.mark.snapshot()
async def test_client_query(claude_agent_sdk, mock_client):
    await mock_client.query(prompt="Hello, world!")
    async for _ in mock_client.receive_messages():
        pass


@pytest.mark.snapshot(ignores=["meta.error.stack"])
async def test_client_query_error(claude_agent_sdk, mock_client_error):
    with pytest.raises(ValueError):
        await mock_client_error.query(prompt="This will fail")


@pytest.mark.snapshot()
async def test_query_with_read_tool_use(claude_agent_sdk, mock_internal_client_tool_use):
    """Test that tool spans are created when tools are used"""
    async for _ in claude_agent_sdk.query(prompt="Read the file at /etc/hostname"):
        pass


@pytest.mark.snapshot()
async def test_query_with_bash_tool_use(claude_agent_sdk, mock_internal_client_bash_tool):
    """Test that tool spans are created for Bash tool"""
    async for _ in claude_agent_sdk.query(prompt="Run 'echo hello' using the Bash tool"):
        pass


@pytest.mark.snapshot()
async def test_query_with_grep_tool_use(claude_agent_sdk, mock_internal_client_grep_tool):
    """Test that tool spans are created for Grep tool"""
    async for _ in claude_agent_sdk.query(prompt="Use the Grep tool to search for 'def test_' in tests"):
        pass

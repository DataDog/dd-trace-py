"""TEST ONLY: synthetic auth. Never use this auth handler in a deployed gateway."""

from fastapi import HTTPException
from litellm.proxy._types import UserAPIKeyAuth


async def authenticate(request, api_key):
    if api_key not in ("test-alice", "test-bob", "test-unassigned"):
        raise HTTPException(401, "Invalid synthetic test credential")
    if api_key == "test-unassigned":
        return UserAPIKeyAuth()
    user = api_key.removeprefix("test-")
    return UserAPIKeyAuth(
        user_id=user,
        user_email=f"{user}@example.test",
        team_id="test-team",
        metadata={"cost_center": "test-eng"},
    )

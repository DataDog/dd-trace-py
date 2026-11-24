from typing import Any

from django.urls import get_resolver as get_resolver


def user_is_authenticated(user: Any) -> bool:
    # Explicit comparison due to the following bug
    # https://code.djangoproject.com/ticket/26988
    return user.is_authenticated == True  # type: ignore[no-any-return] # noqa E712

from typing import Any

import django


if django.VERSION >= (1, 10, 1):
    from django.urls import get_resolver

    def user_is_authenticated(user: Any) -> bool:
        # Explicit comparison due to the following bug
        # https://code.djangoproject.com/ticket/26988
        return user.is_authenticated == True  # type: ignore[no-any-return] # noqa E712

else:
    from django.conf import settings
    from django.core import urlresolvers

    def user_is_authenticated(user: Any) -> bool:
        return user.is_authenticated()  # type: ignore[no-any-return]

    if django.VERSION >= (1, 9, 0):

        def get_resolver(urlconf=None):
            urlconf = urlconf or settings.ROOT_URLCONF
            urlresolvers.set_urlconf(urlconf)
            return urlresolvers.get_resolver(urlconf)

    else:

        def get_resolver(urlconf=None):
            urlconf = urlconf or settings.ROOT_URLCONF
            urlresolvers.set_urlconf(urlconf)
            return urlresolvers.RegexURLResolver(r"^/", urlconf)

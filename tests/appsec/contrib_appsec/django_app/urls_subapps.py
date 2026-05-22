"""Sub-application variant of the Django test urlconf.

All views are reused from urls.py, but grouped endpoints are mounted behind
django.urls.include()-based sub-urlconfs instead of being registered directly
on the root urlpatterns. This exercises the sub-application code paths in the
tracing and AppSec integrations (route computation, endpoint discovery, etc.).

Bare (no-trailing-slash) aliases for /asm, /login, and /login_sdk are registered
directly at the root — matching flat's "^asm/?$" regex and "login"/"login_sdk"
paths — so the overall route surface stays equivalent to urls.py even though the
raw pattern strings differ between variants.
"""

import django
from django.urls import include

from tests.appsec.contrib_appsec.django_app.urls import exception_group_block
from tests.appsec.contrib_appsec.django_app.urls import healthcheck
from tests.appsec.contrib_appsec.django_app.urls import login_user
from tests.appsec.contrib_appsec.django_app.urls import login_user_sdk
from tests.appsec.contrib_appsec.django_app.urls import multi_view
from tests.appsec.contrib_appsec.django_app.urls import new_service
from tests.appsec.contrib_appsec.django_app.urls import rasp
from tests.appsec.contrib_appsec.django_app.urls import redirect
from tests.appsec.contrib_appsec.django_app.urls import redirect_httpx
from tests.appsec.contrib_appsec.django_app.urls import redirect_httpx_async
from tests.appsec.contrib_appsec.django_app.urls import redirect_requests


if django.VERSION < (4, 0, 0):
    from django.conf.urls import url as handler
else:
    from django.urls import re_path as handler

if django.VERSION >= (2, 0, 0):
    from django.urls import path
else:
    from django.conf.urls import url as path


# --- /asm sub-app ---
asm_urls = [
    path("", multi_view, name="multi_view_root"),
    path("<int:param_int>/<str:param_str>/", multi_view, name="multi_view"),
    path("<int:param_int>/<str:param_str>", multi_view, name="multi_view"),
]

# --- /new_service sub-app ---
new_service_urls = [
    path("<str:service_name>/", new_service, name="new_service"),
    path("<str:service_name>", new_service, name="new_service"),
]

# --- /rasp sub-app ---
rasp_urls = [
    path("<str:endpoint>/", rasp, name="rasp"),
    path("<str:endpoint>", rasp, name="rasp"),
]

# --- /redirect* sub-apps ---
redirect_urls = [
    path("<str:route>/<int:port>", redirect, name="redirect"),
]
redirect_requests_urls = [
    path("<str:route>/<int:port>", redirect_requests, name="redirect_requests"),
]
redirect_httpx_urls = [
    path("<str:route>/<int:port>", redirect_httpx, name="redirect_httpx"),
]
redirect_httpx_async_urls = [
    path("<str:route>/<int:port>", redirect_httpx_async, name="redirect_httpx_async"),
]

# --- /login sub-app ---
login_urls = [
    path("", login_user, name="login"),
]

# --- /login_sdk sub-app ---
login_sdk_urls = [
    path("", login_user_sdk, name="login_sdk"),
]


urlpatterns = [
    handler(r"^$", healthcheck),
]

if django.VERSION >= (2, 0, 0):
    urlpatterns += [
        # Bare aliases — equivalent to the flat urlconf's "^asm/?$" regex and
        # its bare "login" / "login_sdk" paths. Kept at the root so the subapp
        # variant exposes the same reachable URLs as the flat one.
        path("asm", multi_view, name="multi_view_bare"),
        path("login", login_user, name="login_bare"),
        path("login_sdk", login_user_sdk, name="login_sdk_bare"),
        # Sub-application mounts.
        path("asm/", include(asm_urls)),
        path("new_service/", include(new_service_urls)),
        path("rasp/", include(rasp_urls)),
        path("redirect/", include(redirect_urls)),
        path("redirect_requests/", include(redirect_requests_urls)),
        path("redirect_httpx/", include(redirect_httpx_urls)),
        path("redirect_httpx_async/", include(redirect_httpx_async_urls)),
        path("login/", include(login_urls)),
        path("login_sdk/", include(login_sdk_urls)),
        path("exception-group-block", exception_group_block, name="exception_group_block"),
    ]

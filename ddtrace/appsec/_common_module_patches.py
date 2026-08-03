"""Lifecycle coordinator for AppSec's common-library patches."""

from ddtrace.appsec._contrib.builtins import patch as builtins_rasp_patch
from ddtrace.appsec._contrib.dbapi import patch as dbapi_rasp_patch
from ddtrace.appsec._contrib.httplib import patch as httplib_rasp_patch
from ddtrace.appsec._contrib.pathlib import patch as pathlib_rasp_patch
from ddtrace.appsec._contrib.stripe import patch as stripe_patch
from ddtrace.appsec._contrib.subprocess import patch as subprocess_rasp_patch
from ddtrace.appsec._contrib.urllib import patch as urllib_rasp_patch
from ddtrace.appsec._contrib.urllib3 import patch as urllib3_rasp_patch
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

_is_patched = False


def patch_common_modules() -> None:
    global _is_patched

    # AIDEV-NOTE: subprocess is patched before the _is_patched guard on purpose: one-click remote
    # activation can call this again after subprocess was imported. The subprocess patch owns its
    # idempotency, so this restores its lifecycle without duplicating the ModuleWatchdog hook.
    subprocess_rasp_patch.patch()
    if _is_patched:
        return

    builtins_rasp_patch.patch()
    pathlib_rasp_patch.patch()
    urllib_rasp_patch.patch()
    httplib_rasp_patch.patch()
    urllib3_rasp_patch.patch()
    stripe_patch.patch()
    dbapi_rasp_patch.patch()

    log.debug("Patching common AppSec modules")
    _is_patched = True


def unpatch_common_modules() -> None:
    global _is_patched
    if not _is_patched:
        return

    builtins_rasp_patch.unpatch()
    pathlib_rasp_patch.unpatch()
    urllib_rasp_patch.unpatch()
    httplib_rasp_patch.unpatch()
    urllib3_rasp_patch.unpatch()
    stripe_patch.unpatch()
    subprocess_rasp_patch.unpatch()
    # AIDEV-NOTE: DBAPI installs only a core listener, so its teardown resets that listener for test
    # lifecycle isolation.
    dbapi_rasp_patch.unpatch()

    log.debug("Unpatching common AppSec modules")
    _is_patched = False

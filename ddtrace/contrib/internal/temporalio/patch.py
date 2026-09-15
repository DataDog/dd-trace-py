import temporalio

from ddtrace import config


config._add("temporalio", {})  # type: ignore[no-untyped-call]


def get_version() -> str:
    return str(getattr(temporalio, "__version__", ""))


def _supported_versions() -> dict[str, str]:
    return {"temporalio": ">=1.0.0"}


def patch() -> None:
    if getattr(temporalio, "_datadog_patch", False):
        return
    temporalio._datadog_patch = True


def unpatch() -> None:
    if not getattr(temporalio, "_datadog_patch", False):
        return
    temporalio._datadog_patch = False

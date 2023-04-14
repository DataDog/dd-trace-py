import os

from ddtrace.vendor.debtcollector import deprecate


DEFAULT_REMOTECONFIG_POLL_SECONDS = 5.0  # seconds


def get_poll_interval_seconds():
    # type:() -> float
    if os.getenv("DD_REMOTECONFIG_POLL_SECONDS"):
        deprecate(
            "Using environment variable 'DD_REMOTECONFIG_POLL_SECONDS' is deprecated",
            message="Please use DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS instead.",
            removal_version="2.0.0",
        )
    return float(
        os.getenv(
            "DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS",
            default=os.getenv("DD_REMOTECONFIG_POLL_SECONDS", default=DEFAULT_REMOTECONFIG_POLL_SECONDS),
        )
    )

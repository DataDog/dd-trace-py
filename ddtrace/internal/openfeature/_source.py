"""
Source-mode configuration for Feature Flagging and Experimentation.
"""

import dataclasses
import enum
import os
import typing as t
from urllib.parse import urlparse


DEFAULT_CDN_BASE_URL = "https://api.datadoghq.com/api/v2/feature-flags/ufc"
DEFAULT_CDN_POLL_INTERVAL_SECONDS = 30.0
DEFAULT_CDN_REQUEST_TIMEOUT_SECONDS = 2.0
DEFAULT_CDN_MAX_RETRIES = 2
DEFAULT_CDN_BACKOFF_BASE_SECONDS = 0.25


class FeatureFlagSourceMode(enum.Enum):
    CDN = "cdn"
    REMOTE_CONFIG = "remote_config"
    OFFLINE = "offline"


@dataclasses.dataclass(frozen=True)
class FeatureFlagCdnConfig:
    base_url: str = DEFAULT_CDN_BASE_URL
    api_key: t.Optional[str] = None
    poll_interval_seconds: float = DEFAULT_CDN_POLL_INTERVAL_SECONDS
    request_timeout_seconds: float = DEFAULT_CDN_REQUEST_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_CDN_MAX_RETRIES
    backoff_base_seconds: float = DEFAULT_CDN_BACKOFF_BASE_SECONDS


@dataclasses.dataclass(frozen=True)
class FeatureFlagSourceConfig:
    mode: FeatureFlagSourceMode
    cdn: FeatureFlagCdnConfig = dataclasses.field(default_factory=FeatureFlagCdnConfig)


def resolve_feature_flag_source_config(env: t.Optional[t.Mapping[str, str]] = None) -> FeatureFlagSourceConfig:
    env = os.environ if env is None else env
    mode = _resolve_source_mode(env.get("DD_FLAGGING_SOURCE_MODE"))
    cdn = FeatureFlagCdnConfig(
        base_url=_resolve_cdn_base_url(env.get("DD_FLAGGING_CDN_BASE_URL")),
        api_key=_optional_value(env.get("DD_API_KEY")),
        poll_interval_seconds=_resolve_positive_float(
            env.get("DD_FLAGGING_CDN_POLL_INTERVAL_SECONDS"),
            DEFAULT_CDN_POLL_INTERVAL_SECONDS,
            "DD_FLAGGING_CDN_POLL_INTERVAL_SECONDS",
        ),
        request_timeout_seconds=_resolve_positive_float(
            env.get("DD_FLAGGING_CDN_REQUEST_TIMEOUT_SECONDS"),
            DEFAULT_CDN_REQUEST_TIMEOUT_SECONDS,
            "DD_FLAGGING_CDN_REQUEST_TIMEOUT_SECONDS",
        ),
        max_retries=DEFAULT_CDN_MAX_RETRIES,
        backoff_base_seconds=DEFAULT_CDN_BACKOFF_BASE_SECONDS,
    )
    return FeatureFlagSourceConfig(mode=mode, cdn=cdn)


def _resolve_source_mode(raw_mode: t.Optional[str]) -> FeatureFlagSourceMode:
    if raw_mode is None or raw_mode.strip() == "":
        return FeatureFlagSourceMode.CDN
    normalized = raw_mode.strip().lower().replace("-", "_")
    try:
        return FeatureFlagSourceMode(normalized)
    except ValueError:
        valid = ", ".join(mode.value for mode in FeatureFlagSourceMode)
        raise ValueError("Invalid DD_FLAGGING_SOURCE_MODE %r. Expected one of: %s" % (raw_mode, valid))


def _resolve_cdn_base_url(raw_url: t.Optional[str]) -> str:
    base_url = _optional_value(raw_url) or DEFAULT_CDN_BASE_URL
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("DD_FLAGGING_CDN_BASE_URL must be an absolute http or https URL")
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("DD_FLAGGING_CDN_BASE_URL may use plain HTTP only for localhost test endpoints")
    return base_url


def _resolve_positive_float(raw_value: t.Optional[str], default: float, env_name: str) -> float:
    if raw_value is None or raw_value.strip() == "":
        return default
    try:
        value = float(raw_value)
    except ValueError:
        raise ValueError("%s must be a positive number" % env_name)
    if value <= 0:
        raise ValueError("%s must be a positive number" % env_name)
    return value


def _optional_value(value: t.Optional[str]) -> t.Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value or None

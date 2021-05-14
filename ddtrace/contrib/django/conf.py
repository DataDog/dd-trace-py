"""
Settings for Datadog tracer are all namespaced in the DATADOG_TRACE setting.
For example your project's `settings.py` file might look like this::

    DATADOG_TRACE = {
        'TRACER': 'myapp.tracer',
    }

This module provides the `setting` object, that is used to access
Datadog settings, checking for user settings first, then falling
back to the defaults.
"""
from __future__ import unicode_literals

import importlib
import os

from django.conf import settings as django_settings

from ...internal.logger import get_logger


log = get_logger(__name__)

# List of available settings with their defaults
DEFAULTS = {
    "AGENT_HOSTNAME": "localhost",
    "AGENT_PORT": 8126,
    "AUTO_INSTRUMENT": True,
    "INSTRUMENT_CACHE": True,
    "INSTRUMENT_DATABASE": True,
    "INSTRUMENT_TEMPLATE": True,
    "DEFAULT_DATABASE_PREFIX": "",
    "DEFAULT_SERVICE": "django",
    "DEFAULT_CACHE_SERVICE": "",
    "ENABLED": True,
    "DISTRIBUTED_TRACING": True,
    "ANALYTICS_ENABLED": None,
    "ANALYTICS_SAMPLE_RATE": True,
    "TRACE_QUERY_STRING": None,
    "TAGS": {},
    "TRACER": "ddtrace.tracer",
}

# List of settings that may be in string import notation.
IMPORT_STRINGS = ("TRACER",)

# List of settings that have been removed
REMOVED_SETTINGS = ()


def import_from_string(val, setting_name):
    """
    Attempt to import a class from a string representation.
    """
    try:
        # Nod to tastypie's use of importlib.
        parts = val.split(".")
        module_path, class_name = ".".join(parts[:-1]), parts[-1]
        module = importlib.import_module(module_path)
        return getattr(module, class_name)
    except (ImportError, AttributeError) as e:
        msg = 'Could not import "{}" for setting "{}". {}: {}.'.format(
            val,
            setting_name,
            e.__class__.__name__,
            e,
        )

        raise ImportError(msg)


class DatadogSettings(object):
    """
    A settings object, that allows Datadog settings to be accessed as properties.
    For example:

        from ddtrace.contrib.django.conf import settings

        tracer = settings.TRACER

    Any setting with string import paths will be automatically resolved
    and return the class, rather than the string literal.
    """

    def __init__(self, user_settings=None, defaults=None, import_strings=None):
        if user_settings:
            self._user_settings = self.__check_user_settings(user_settings)

        self.defaults = defaults or DEFAULTS
        if os.environ.get("DATADOG_ENV"):
            self.defaults["TAGS"].update({"env": os.environ.get("DATADOG_ENV")})

        for env_name in ("DATADOG_SERVICE_NAME", "DD_SERVICE_NAME"):
            if os.environ.get(env_name):
                self.defaults["DEFAULT_SERVICE"] = os.environ[env_name]

        host = os.environ.get("DD_AGENT_HOST", os.environ.get("DATADOG_TRACE_AGENT_HOSTNAME"))
        if host:
            self.defaults["AGENT_HOSTNAME"] = host

        port = os.environ.get("DD_TRACE_AGENT_PORT", os.environ.get("DATADOG_TRACE_AGENT_PORT"))
        if port:
            # if the agent port is a string, the underlying library that creates the socket
            # stops working
            try:
                port = int(port)
            except ValueError:
                log.warning("DD_TRACE_AGENT_PORT is not an integer value; default to 8126")
            else:
                self.defaults["AGENT_PORT"] = port

        self.import_strings = import_strings or IMPORT_STRINGS

    @property
    def user_settings(self):
        if not hasattr(self, "_user_settings"):
            self._user_settings = getattr(django_settings, "DATADOG_TRACE", {})

        # TODO[manu]: prevents docs import errors; provide a better implementation
        if "ENABLED" not in self._user_settings:
            self._user_settings["ENABLED"] = not django_settings.DEBUG
        return self._user_settings

    def __getattr__(self, attr):
        if attr not in self.defaults:
            raise AttributeError('Invalid setting: "{}"'.format(attr))

        try:
            # Check if present in user settings
            val = self.user_settings[attr]
        except KeyError:
            # Otherwise, fall back to defaults
            val = self.defaults[attr]

        # Coerce import strings into classes
        if attr in self.import_strings:
            val = import_from_string(val, attr)

        # Cache the result
        setattr(self, attr, val)
        return val

    def __check_user_settings(self, user_settings):
        SETTINGS_DOC = "https://ddtrace.readthedocs.io/en/stable/integrations.html#django"
        for setting in REMOVED_SETTINGS:
            if setting in user_settings:
                raise RuntimeError('The "{}" setting has been removed, check "{}".'.format(setting, SETTINGS_DOC))
        return user_settings


settings = DatadogSettings(None, DEFAULTS, IMPORT_STRINGS)


def reload_settings(*args, **kwargs):
    """
    Triggers a reload when Django emits the reloading signal
    """
    global settings
    setting, value = kwargs["setting"], kwargs["value"]
    if setting == "DATADOG_TRACE":
        settings = DatadogSettings(value, DEFAULTS, IMPORT_STRINGS)


# Migration from installing and configuring ddtrace as a Django middleware app

_MAPPING_SETTINGS_CONFIG = dict(
    DEFAULT_SERVICE="service_name",
    DEFAULT_CACHE_SERVICE="cache_service_name",
    DEFAULT_DATABASE_PREFIX="database_service_name_prefix",
    DISTRIBUTED_TRACING="distributed_tracing_enabled",
    INSTRUMENT_DATABASES="instrument_databases",
    INSTRUMENT_CACHE="instrument_caches",
    ANALYTICS_ENABLED="analytics_enabled",
    ANALYTICS_SAMPLE_RATE="analytics_sample_rate",
    TRACE_QUERY_STRING="trace_query_string",
)


def configure_from_settings(pin, config, settings):
    # set config options from settings using mapping
    for (setting_key, config_key) in _MAPPING_SETTINGS_CONFIG.items():
        if setting_key in settings:
            setattr(config, config_key, settings[setting_key])

    # re-configure tracer
    if "TRACE" in settings:
        pin.tracer = settings["TRACER"]

    if "TAGS" in settings:
        pin.tracer.set_tags(settings["TAGS"])

    if "ENABLED" in settings:
        pin.tracer.enabled = settings["ENABLED"]

    if "AGENT_HOSTNAME" in settings:
        pin.tracer.configure(hostname=settings["AGENT_HOSTNAME"])

    if "AGENT_PORT" in settings:
        pin.tracer.configure(port=settings["AGENT_PORT"])

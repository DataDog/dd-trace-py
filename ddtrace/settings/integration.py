from copy import deepcopy

from ..vendor import attr
from ..utils.formats import asbool, get_env
from .http import HttpConfig
from .hooks import Hooks


@attr.s
class Setting(object):
    """Setting represents a configuration setting which is defined by the
    library and optionally overridden by the user.
    """

    Undefined = object()

    @attr.s
    class Defined(object):
        value = attr.ib()

    class SettingKeyError(KeyError):
        pass

    _value = attr.ib(default=Undefined, validator=attr.validators.instance_of(Defined))
    _default = attr.ib(init=False)

    def __attrs_post_init__(self):
        self._default = self._value

    def set(self, value):
        self._value = value
        return value

    def get(self):
        if self._value is self.Undefined:
            raise self.SettingKeyError()
        return self._value.value

    def reset(self):
        self._value = self._default

    def isdefined(self):
        return self._value is not self.Undefined

    def check(self, _type):
        return isinstance(self._value, _type)


class IntegrationConfig(object):
    """
    All internal methods/properties must be private (prefixed with _)

    - Differentiation between library and user defined values
    - Support for defaults
    - User API is limited to reading/writing only settings that exist
    - Awareness of whether a setting has been overridden by a user

    Library API::

        from ddtrace import config
        config._add("redis", dict(
            service="redis",
        ))
        config.redis._get("service")


    User API::

        from ddtrace import config
        config.redis.service = "test"
        config.redis.serivce = "test"  # throws exception!!

    """

    class UserDefined(Setting.Defined):
        pass

    class LibDefined(Setting.Defined):
        pass

    class IntegrationConfigAttributeError(AttributeError):
        pass

    class IntegrationConfigKeyError(KeyError):
        pass

    @attr.s
    class Dec(object):
        """Used to declare a setting for an IntegrationConfig
        """
        default = attr.ib()
        env_var = attr.ib(default=False, type=bool)
        type = attr.ib(default=None)

        def cast(self, val):
            if self.type is None:
                return val
            elif self.type is bool:
                return asbool(val)
            elif self.type is float:
                return float(val)

            raise NotImplementedError("type {} not implemented".format(self.type))

    def __init__(self, global_config, name, *args, **kwargs):
        self._global_config = global_config
        self._name = name
        self._settings = {}
        self._hooks = Hooks()
        self._http = HttpConfig()

        # Default integration settings
        init_settings = dict(
            analytics_enabled=self.Dec(False, type=bool, env_var=True),
            analytics_sample_rate=self.Dec(1.0, type=float, env_var=True),
            service_name=self.Dec(None),
        )
        init_settings.update(dict(*args, **kwargs))

        # Defaults can be overridden by integration-specific configs
        for (key, setting) in init_settings.items():
            if not isinstance(setting, self.Dec):
                setting = self.Dec(setting)

            # First add the setting with the default.
            self._add(key, setting.default)

            # Then perform any environment variable lookups
            if setting.env_var:
                env_val = get_env(name, key)
                if env_val is not None:
                    self._set_user(key, setting.cast(env_val))

    @property
    def http(self):
        return self._http

    @property
    def hooks(self):
        return self._hooks

    @property
    def global_config(self):
        return self._global_config

    def copy(self):
        return self

    def __deepcopy__(self, memodict=None):
        new = IntegrationConfig(self._global_config, self._name, deepcopy(self._settings))
        new._hooks = deepcopy(self._hooks)
        new._http = deepcopy(self._http)
        return new

    def __getitem__(self, key):
        # implement Config()[] operator
        if key not in self._settings:
            raise self.IntegrationConfigKeyError("Setting {} does not exist".format(key))
        return self._settings[key].get()

    def __setitem__(self, key, value):
        # implement Config()[] = operator
        if key not in self._settings:
            raise self.IntegrationConfigKeyError("Setting {} does not exist".format(key))
        return self._set_user(key, value)

    def __contains__(self, key):
        # implement in operator
        return key in self._settings

    def __getattr__(self, key):
        """
        getattr is only called if the attribute does not exist
        """
        if key.startswith("_"):
            return object.__getattribute__(self, key)

        if key in self._settings:
            return self._settings[key].get()

        try:
            return object.__getattribute__(self, key)
        except AttributeError as e:
            raise self.IntegrationConfigAttributeError(e)

    def __setattr__(self, key, value):
        if key.startswith("_"):
            return object.__setattr__(self, key, value)
        if key in self._settings:
            return self._set_user(key, value)
        else:
            raise self.IntegrationConfigAttributeError("Setting for key {} does not exist".format(key))

    def _add(self, key, value):
        self._settings[key] = Setting(self.LibDefined(value))

    def _reset(self):
        for key in self._settings:
            self._settings[key].reset()

    def _set_user(self, key, value):
        self._settings[key].set(self.UserDefined(value))
        return value

    def _is_user_defined(self, key):
        return self._settings[key].check(IntegrationConfig.UserDefined)

    def _is_lib_defined(self, key):
        return self._settings[key].check(IntegrationConfig.LibDefined)

    def items(self):
        return [
            (k, v.get()) for k, v in self._settings.items()
        ]

    def get(self, key, default=Setting.Undefined):
        if key not in self._settings and default is not Setting.Undefined:
            return default
        return self._settings[key].get()

    def update(self, other):
        for k, v in other.items():
            self._set_user(k, v)
        return self

    @property
    def trace_query_string(self):
        if self._http.trace_query_string is not None:
            return self._http.trace_query_string
        return self._global_config._http.trace_query_string

    def header_is_traced(self, header_name):
        """
        Returns whether or not the current header should be traced.
        :param header_name: the header name
        :type header_name: str
        :rtype: bool
        """
        return (
            self._http.header_is_traced(header_name)
            if self._http.is_header_tracing_configured
            else self._global_config.header_is_traced(header_name)
        )

    def _is_analytics_enabled(self, use_global_config):
        if use_global_config and self._global_config.analytics_enabled:
            # Allow users to override the global
            if self._is_user_defined("analytics_enabled"):
                return self.analytics_enabled
            return True
        else:
            return self.analytics_enabled

    def get_analytics_sample_rate(self, use_global_config=False):
        """
        Returns analytics sample rate but only when integration-specific
        analytics configuration is enabled with optional override with global
        configuration
        """
        if self._is_analytics_enabled(use_global_config):
            # return True if attribute is None or attribute not found
            if self.analytics_sample_rate is None:
                return True
            # otherwise return rate
            return self.analytics_sample_rate

        # Use `None` as a way to say that it was not defined,
        #   `False` would mean `0` which is a different thing
        return None

    def __repr__(self):
        cls = self.__class__
        keys = ", ".join(["{}={}".format(k, v) for k, v in self._settings.items()])
        return "{}.{}({})".format(cls.__module__, cls.__name__, keys)

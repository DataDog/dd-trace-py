import collections
from copy import deepcopy
import logging

from .pin import Pin
from .span import Span
from .utils.formats import get_env
from .utils.merge import deepmerge
from .utils.http import normalize_header_name


log = logging.getLogger(__name__)


class ConfigException(Exception):
    """Configuration exception when an integration that is not available
    is called in the `Config` object.
    """
    pass


class Config(object):
    """Configuration object that exposes an API to set and retrieve
    global settings for each integration. All integrations must use
    this instance to register their defaults, so that they're public
    available and can be updated by users.
    """
    def __init__(self):
        # use a dict as underlying storing mechanism
        self._config = {}
        self._http = HttpConfig()

    def __getattr__(self, name):
        if name not in self._config:
            self._config[name] = IntegrationConfig(self, name)
        return self._config[name]

    def get_from(self, obj):
        """Retrieves the configuration for the given object.
        Any object that has an attached `Pin` must have a configuration
        and if a wrong object is given, an empty `dict` is returned
        for safety reasons.
        """
        pin = Pin.get_from(obj)
        if pin is None:
            log.debug('No configuration found for %s', obj)
            return {}

        return pin._config

    def _add(self, integration, settings, merge=True):
        """Internal API that registers an integration with given default
        settings.

        :param str integration: The integration name (i.e. `requests`)
        :param dict settings: A dictionary that contains integration settings;
            to preserve immutability of these values, the dictionary is copied
            since it contains integration defaults.
        :param bool merge: Whether to merge any existing settings with those provided,
            or if we should overwrite the settings with those provided;
            Note: when merging existing settings take precedence.
        """
        # DEV: Use `getattr()` to call our `__getattr__` helper
        existing = getattr(self, integration)
        settings = deepcopy(settings)

        if merge:
            # DEV: This may appear backwards keeping `existing` as the "source" and `settings` as
            #   the "destination", but we do not want to let `_add(..., merge=True)` overwrite any
            #   existing settings
            #
            # >>> config.requests['split_by_domain'] = True
            # >>> config._add('requests', dict(split_by_domain=False))
            # >>> config.requests['split_by_domain']
            # True
            self._config[integration] = IntegrationConfig(self, integration, defaults=deepmerge(existing, settings))
        else:
            self._config[integration] = IntegrationConfig(self, integration, defaults=settings)

    def trace_headers(self, whitelist):
        """
        Registers a set of headers to be traced at global level or integration level.
        :param whitelist: the case-insensitive list of traced headers
        :type whitelist: list of str or str
        :return: self
        :rtype: HttpConfig
        """
        self._http.trace_headers(whitelist)
        return self

    def header_is_traced(self, header_name):
        """
        Returns whether or not the current header should be traced.
        :param header_name: the header name
        :type header_name: str
        :rtype: bool
        """
        return self._http.header_is_traced(header_name)

    def __repr__(self):
        cls = self.__class__
        integrations = ', '.join(self._config.keys())
        return '{}.{}({})'.format(cls.__module__, cls.__name__, integrations)


class IntegrationConfigItem(object):
    """
    A wrapper for items stored in an IntegrationConfig which is responsible for
    determining what the value of the item stored in the config should be.

    The precedence for determining the value for a config item is:

    [high]
        1) user-overridden value (indicated via `update_value`)
        2) environment variable  (of the form DD_{INTEGRATION}_{KEY})
        3) default value         (specified in the initializer)
    [low]

    TODO:
        - env_var_names param to allow custom environment variables to be checked
        - docstring param to allow the item to be described by the integration
        - provide a method for the item to be reset back to the default after a
          user has overridden and wants to remove their override.
        - global config fallback when no default is provided.
    """
    def __init__(self, config, key, default_value=None):
        self.config = config
        self.default_value = default_value
        self.key = key
        self._value = None
        # Flag indicating whether the value has been defined by the user or not,
        # by default it is not.
        self.user_defined = False

    def update_value(self, value):
        self.user_defined = True
        self._value = value

    def _get_from_env(self):
        env_val = get_env(self.config._integration_name, self.key)
        return env_val

    @property
    def value(self):
        if self.user_defined:
            return self._value

        env_val = self._get_from_env()
        if env_val:
            return env_val

        return self.default_value


class IntegrationConfig(object):
    """
    Integration specific configuration object.

    This is what you will get when you do::

        from ddtrace import config

        # This is an `IntegrationConfig`
        config.flask

        # `IntegrationConfig` supports both attribute and item accessors
        config.flask['service_name'] = 'my-service-name'
        config.flask.service_name = 'my-service-name'
    """
    _initialized = False

    def __init__(self, global_config, integration_name, defaults=None):
        """
        :param global_config: a reference back to the global config
        :param integration_name: the name of the integration for this config
        :param defaults: default key, value pairs to add
        :type defaults: dict
        """
        self.global_config = global_config
        self.hooks = Hooks()
        self.http = HttpConfig()
        self._integration_name = integration_name
        self._items = dict()

        # Add the default items to the config if they are specified
        defaults = defaults or {}
        for key in defaults:
            self._add_default(key, defaults[key])

        # This flag is for the __setattr__ to know when we have finished setting
        # attributes on the class.
        self._initialized = True

    def __getattr__(self, item):
        # Use the .items() of the config items (stored in self.items)
        if item == 'items':
            return self._items.items

        if item not in self._items:
            # This will intentionally raise an AttributeError for the config
            return object.__getattribute__(self, item)

        item = self._items[item]
        return item.value

    def __getitem__(self, item):
        return getattr(self, item)

    def __setattr__(self, key, value):
        # If the class attributes have not yet been initialized or the key is
        # an attribute on this class then just use the normal object.__setattr__
        if not self._initialized or hasattr(super(IntegrationConfig, self), key):
            return object.__setattr__(self, key, value)

        # Else we have either a new attribute or an attribute to be stored as a
        # config item.

        if not hasattr(self, key):
            # If the attribute does not exist, add it as a default config item.
            # TODO: should this be a no-op with a log message instead?
            item = self._add_default(key, value)
        else:
            item = self._items[key]
            item.update_value(value)
            self._items[key] = item
        return item.value

    def __setitem__(self, key, value):
        return setattr(self, key, value)

    def __deepcopy__(self, memodict=None):
        new = IntegrationConfig(self.global_config, self.integration_name, deepcopy(dict(self)))
        new.hooks = deepcopy(self.hooks)
        new.http = deepcopy(self.http)
        return new

    def _add_default(self, key, value):
        """Adds a default key and value to the configuration.

        :param key: key of the item
        :param value: value of the item
        :return: the IntegrationConfigItem for the config item
        """
        item = IntegrationConfigItem(self, key, default_value=value)
        if key in self._items:
            log.warning('_add_default key already exists')
        self._items[key] = item
        return item

    def header_is_traced(self, header_name):
        """
        Returns whether or not the current header should be traced.
        :param header_name: the header name
        :type header_name: str
        :rtype: bool
        """
        return (
            self.http.header_is_traced(header_name)
            if self.http.is_header_tracing_configured
            else self.global_config.header_is_traced(header_name)
        )

    def __repr__(self):
        cls = self.__class__
        keys = ', '.join(self.keys())
        return '{}.{}({})'.format(cls.__module__, cls.__name__, keys)


class Hooks(object):
    """
    Hooks configuration object is used for registering and calling hook functions

    Example::

        @config.falcon.hooks.on('request')
        def on_request(span, request, response):
            pass
    """
    __slots__ = ['_hooks']

    def __init__(self):
        self._hooks = collections.defaultdict(set)

    def __deepcopy__(self, memodict=None):
        hooks = Hooks()
        hooks._hooks = deepcopy(self._hooks)
        return hooks

    def register(self, hook, func=None):
        """
        Function used to register a hook for the provided name.

        Example::

            def on_request(span, request, response):
                pass

            config.falcon.hooks.register('request', on_request)


        If no function is provided then a decorator is returned::

            @config.falcon.hooks.register('request')
            def on_request(span, request, response):
                pass

        :param hook: The name of the hook to register the function for
        :type hook: str
        :param func: The function to register, or ``None`` if a decorator should be returned
        :type func: function, None
        :returns: Either a function decorator if ``func is None``, otherwise ``None``
        :rtype: function, None
        """
        # If they didn't provide a function, then return a decorator
        if not func:
            def wrapper(func):
                self.register(hook, func)
                return func
            return wrapper
        self._hooks[hook].add(func)

    # Provide shorthand `on` method for `register`
    # >>> @config.falcon.hooks.on('request')
    #     def on_request(span, request, response):
    #        pass
    on = register

    def deregister(self, func):
        """
        Function to deregister a function from all hooks it was registered under

        Example::

            @config.falcon.hooks.on('request')
            def on_request(span, request, response):
                pass

            config.falcon.hooks.deregister(on_request)


        :param func: Function hook to register
        :type func: function
        """
        for funcs in self._hooks.values():
            if func in funcs:
                funcs.remove(func)

    def _emit(self, hook, span, *args, **kwargs):
        """
        Function used to call registered hook functions.

        :param hook: The hook to call functions for
        :type hook: str
        :param span: The span to call the hook with
        :type span: :class:`ddtrace.span.Span`
        :param *args: Positional arguments to pass to the hook functions
        :type args: list
        :param **kwargs: Keyword arguments to pass to the hook functions
        :type kwargs: dict
        """
        # Return early if no hooks are registered
        if hook not in self._hooks:
            return

        # Return early if we don't have a Span
        if not isinstance(span, Span):
            return

        # Call registered hooks
        for func in self._hooks[hook]:
            try:
                func(span, *args, **kwargs)
            except Exception as e:
                # DEV: Use log.debug instead of log.error until we have a throttled logger
                log.debug('Failed to run hook {} function {}: {}'.format(hook, func, e))

    def __repr__(self):
        """Return string representation of this class instance"""
        cls = self.__class__
        hooks = ','.join(self._hooks.keys())
        return '{}.{}({})'.format(cls.__module__, cls.__name__, hooks)


class HttpConfig(object):
    """
    Configuration object that expose an API to set and retrieve both global and integration specific settings
    related to the http context.
    """

    def __init__(self):
        self._whitelist_headers = set()

    @property
    def is_header_tracing_configured(self):
        return len(self._whitelist_headers) > 0

    def trace_headers(self, whitelist):
        """
        Registers a set of headers to be traced at global level or integration level.
        :param whitelist: the case-insensitive list of traced headers
        :type whitelist: list of str or str
        :return: self
        :rtype: HttpConfig
        """
        if not whitelist:
            return

        whitelist = [whitelist] if isinstance(whitelist, str) else whitelist
        for whitelist_entry in whitelist:
            normalized_header_name = normalize_header_name(whitelist_entry)
            if not normalized_header_name:
                continue
            self._whitelist_headers.add(normalized_header_name)

        return self

    def header_is_traced(self, header_name):
        """
        Returns whether or not the current header should be traced.
        :param header_name: the header name
        :type header_name: str
        :rtype: bool
        """
        normalized_header_name = normalize_header_name(header_name)
        log.debug('Checking header \'%s\' tracing in whitelist %s', normalized_header_name, self._whitelist_headers)
        return normalized_header_name in self._whitelist_headers

    def __repr__(self):
        return '<HttpConfig traced_headers={}>'.format(self._whitelist_headers)


# Configure our global configuration object
config = Config()

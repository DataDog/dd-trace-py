import collections
from copy import deepcopy
import logging

from .pin import Pin
from .span import Span
from .utils.attrdict import AttrDict
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
        existing = getattr(self, integration)
        for key, value in settings.items():
            if not isinstance(value, ConfigItem):
                value = ConfigItem(key, default=value)
            existing[key] = value
        self._config[integration] = existing

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


class ConfigItem(object):
    __slots__ = ('name', 'value', '_default')

    UNSET = object()

    def __init__(self, name, default=None, doc=None):
        self.name = name
        self.value = ConfigItem.UNSET
        self._default = default

        # TOOD: Set a default __doc__
        if doc is not None:
            self.__doc__ = doc

    def __get__(self, config):
        if self.value is ConfigItem.UNSET:
            return get_env(config.integration_name, self.name, default=self._default)
        return self.value

    def __set__(self, config, value):
        if isinstance(value, ConfigItem):
            self._default = value._default
        else:
            self.value = value

    def set_default(self, default):
        self._default = default

    def __repr__(self):
        value = self.value if self.value is not ConfigItem.UNSET else self._default

        return '{0}(name={1!r}, value={2!r})'.format(
            self.__class__.__name__,
            self.name,
            value,
        )

    def __depcopy__(self, memodict=None):
        c = ConfigItem(self.name, default=self._default, doc=self.__doc__)
        c.value = self.value
        return c

    def copy(self, memodict=None):
        c = ConfigItem(self.name, default=self._default, doc=self.__doc__)
        c.value = self.value
        return c


class IntegrationConfig(AttrDict):
    __slots__ = ('integration_name', 'global_config', 'hooks', 'http')

    def __init__(self, integration_name, global_config, defaults=None):
        defaults = defaults or dict()
        defaults['event_sample_rate'] = ConfigItem('event_sample_rate')
        super(IntegrationConfig, self).__init__(**defaults)

        attrs = dict(
            integration_name=integration_name,
            global_config=global_config,
            # hooks=Hooks(),
            # http=HttpConfig(),
        )
        for name, value in attrs.items():
            object.__setattr__(self, name, value)

    def __getattr__(self, name):
        try:
            return object.__getattribute__(self, name)
        except AttributeError:
            pass

        if name in self:
            val = self[name]
        else:
            val = self[name] = ConfigItem(name)
        if hasattr(val, '__get__'):
            return val.__get__(self)
        return val

    def __setattr__(self, name, value):
        self[name] = value

    def __getitem__(self, name):
        try:
            value = AttrDict.__getitem__(self, name)
        except KeyError:
            value = ConfigItem(name)
            self[name] = value
        if hasattr(value, '__get__'):
            return value.__get__(self)
        return value

    def __setitem__(self, name, value):
        try:
            item = AttrDict.__getitem__(self, name)
        except KeyError:
            item = ConfigItem(name)
            AttrDict.__setitem__(self, name, item)

        item.__set__(self, value)

    def __repr__(self):
        items = ', '.join([
            '{0}={1!r}'.format(key, value.__get__(self))
            for key, value in self.items()
        ])
        if items:
            items = ', {0}'.format(items)
        return '{0}(integration_name={1!r}{2})'.format(self.__class__.__name__, self.integration_name, items)

    def __deepcopy__(self, memodict=None):
        # DEV: `dict(self)` will give us the values, calling `self.items()` will give us the `ConfigItem`s
        new = IntegrationConfig(self.global_config, self.integration_name, deepcopy(dict(self.items())))
        new.hooks = deepcopy(self.hooks)
        new.http = deepcopy(self.http)
        return new

    def copy(self):
        # DEV: `dict(self)` will give us the values, calling `self.items()` will give us the `ConfigItem`s
        new = IntegrationConfig(self.global_config, self.integration_name, deepcopy(dict(self.items())))
        return new

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

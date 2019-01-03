from copy import deepcopy
import os

from ..utils.attrdict import AttrDict
from ..utils.formats import get_env
from ..utils.merge import deepmerge
from .http import HttpConfig
from .hooks import Hooks


class IntegrationConfigItemBase(object):
    pass


class IntegrationConfigItem(IntegrationConfigItemBase):
    __slots__ = ('name', 'value', 'default', 'env', 'env_override')

    UNSET = object()

    def __init__(self, name, default=None, doc=None, env=True, env_override=None):
        self.name = name
        self.value = IntegrationConfigItem.UNSET
        self.default = default
        self.env_override = env_override
        # DEV: Force `env` to be True if `env_override` is set
        self.env = env or bool(self.env_override)

        # TOOD: Set a default __doc__
        if doc is not None:
            self.__doc__ = doc

    def alias(self, name, doc=None):
        return IntegrationConfigItemAlias(name=name, alias=self.name, doc=doc)

    def __get__(self, config):
        if self.value is IntegrationConfigItem.UNSET:
            # Only fetch env variables if we should
            if self.env:
                # If an environment variable override is set, use that instead
                if self.env_override:
                    return os.environ.get(self.env_override, self.default)

                return get_env(config.integration_name, self.name, default=self.default)
            else:
                return self.default
        return self.value

    def __set__(self, config, new_value):
        if isinstance(new_value, IntegrationConfigItem):
            self.default = new_value.default

            # If we are updating the default when a `dict` value is already set
            # then merge the value and default dict together
            if isinstance(self.value, dict) and isinstance(self.default, dict):
                d = deepcopy(self.default)
                self.value = deepmerge(self.value, d)
        else:
            if isinstance(new_value, dict) and isinstance(self.default, dict):
                d = deepcopy(self.default)
                new_value = deepmerge(new_value, d)
            self.value = new_value

    def __repr__(self):
        value = self.value if self.value is not IntegrationConfigItem.UNSET else self.default

        return '{0}(name={1!r}, value={2!r})'.format(
            self.__class__.__name__,
            self.name,
            value,
        )

    def __deepcopy__(self, memodict=None):
        return self.copy()

    def copy(self):
        c = IntegrationConfigItem(self.name, default=deepcopy(self.default), doc=self.__doc__)
        if c.value is not IntegrationConfigItem.UNSET:
            c.value = deepcopy(self.value)
        return c


class IntegrationConfigItemAlias(IntegrationConfigItemBase):
    __slots__ = ('name', 'alias')

    def __init__(self, name, alias, doc=None):
        self.name = name
        self.alias = alias

        if doc is not None:
            self.__doc__ = doc

    def __get__(self, config):
        item = config.get_item(self.alias)
        if not item:
            return get_env(config.integration_name, self.name)

        if item.value is not IntegrationConfigItem.UNSET:
            return item.value

        return get_env(config.integration_name, self.name, default=item.__get__(config))

    def __set__(self, config, value):
        item = config.get_item(self.alias)
        if item:
            item.__set__(config, value)

    def __repr__(self):
        return '{0}(name={1!r}, alias={2!r})'.format(
            self.__class__.__name__,
            self.name,
            self.alias,
        )

    def __deepcopy__(self, memodict=None):
        return self.copy()

    def copy(self):
        return IntegrationConfigItemAlias(self.name, self.alias)


class IntegrationConfig(AttrDict):
    __slots__ = ('integration_name', 'global_config', 'hooks', 'http')

    def __init__(self, global_config, integration_name, defaults=None):
        defaults = defaults or dict()
        defaults['event_sample_rate'] = IntegrationConfigItem('event_sample_rate')
        super(IntegrationConfig, self).__init__(**defaults)

        # Configure non-IntegrationConfigItem attributes
        attrs = dict(
            integration_name=integration_name,
            global_config=global_config,
            hooks=Hooks(),
            http=HttpConfig(),
        )
        for name, value in attrs.items():
            object.__setattr__(self, name, value)

    def __getattr__(self, name):
        # Look for non-dict properties on this IntegrationConfig first
        # e.g. `hooks`, `http`, `__class__`, etc
        try:
            return object.__getattribute__(self, name)
        except AttributeError:
            pass

        # Get the existing `IntegrationConfigItem` or create a new one if none-exists
        if name in self:
            val = self[name]
        else:
            val = self[name] = IntegrationConfigItem(name)

        # If we have a descriptor then fetch it's value
        if hasattr(val, '__get__'):
            return val.__get__(self)
        return val

    def __setattr__(self, name, value):
        self[name] = value

    def update(self, values=None, **kwargs):
        if hasattr(values, 'items'):
            values = values.items()

        for k, v in values:
            self[k] = v

        for k, v in kwargs.items():
            self[k] = v

    def get(self, name, default=None):
        item = self.get_item(name)
        if item:
            return item.__get__(self)
        return default

    def get_item(self, name):
        try:
            return dict.__getitem__(self, name)
        except KeyError:
            return None

    def __getitem__(self, name):
        # Fetch the existing `IntegrationConfigItem` or create a new one if none was found
        val = self.get_item(name)
        if not val:
            val = IntegrationConfigItem(name)
            self[name] = val

        # If we have a descriptor then fetch it's value
        if hasattr(val, '__get__'):
            return val.__get__(self)
        return val

    def __setitem__(self, name, value):
        # Fetch the existing `IntegrationConfigItem` or create a new one if none was found
        item = self.get_item(name)
        if not item:
            if not isinstance(value, IntegrationConfigItemBase):
                item = IntegrationConfigItem(name)
                item.__set__(self, value)
                dict.__setitem__(self, name, item)
            else:
                dict.__setitem__(self, name, value)
        else:
            # DEV: We should always have an `IntegrationConfigItem` here
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
        # DEV: `dict(self)` will give us the values, calling `self.items()` will give us the `IntegrationConfigItem`s
        new = IntegrationConfig(self.global_config, self.integration_name, deepcopy(dict(self.items())))
        new.hooks = deepcopy(self.hooks)
        new.http = deepcopy(self.http)
        return new

    def copy(self):
        # DEV: `dict(self)` will give us the values, calling `self.items()` will give us the `IntegrationConfigItem`s
        new = IntegrationConfig(
            self.global_config,
            self.integration_name,
            dict([(name, deepcopy(item)) for name, item in self.items()]),
        )
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

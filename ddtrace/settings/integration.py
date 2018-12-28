from copy import deepcopy

from ..utils.attrdict import AttrDict
from ..utils.formats import get_env
from .http import HttpConfig
from .hooks import Hooks


class IntegrationConfigItem(object):
    __slots__ = ('name', 'value', '_default')

    UNSET = object()

    def __init__(self, name, default=None, doc=None):
        self.name = name
        self.value = IntegrationConfigItem.UNSET
        self._default = default

        # TOOD: Set a default __doc__
        if doc is not None:
            self.__doc__ = doc

    def __get__(self, config):
        if self.value is IntegrationConfigItem.UNSET:
            return get_env(config.integration_name, self.name, default=self._default)
        return self.value

    def __set__(self, config, value):
        if isinstance(value, IntegrationConfigItem):
            self._default = value._default
        else:
            self.value = value

    def set_default(self, default):
        self._default = default

    def __repr__(self):
        value = self.value if self.value is not IntegrationConfigItem.UNSET else self._default

        return '{0}(name={1!r}, value={2!r})'.format(
            self.__class__.__name__,
            self.name,
            value,
        )

    def __depcopy__(self, memodict=None):
        c = IntegrationConfigItem(self.name, default=self._default, doc=self.__doc__)
        c.value = self.value
        return c

    def copy(self, memodict=None):
        c = IntegrationConfigItem(self.name, default=self._default, doc=self.__doc__)
        c.value = self.value
        return c


class IntegrationConfig(AttrDict):
    __slots__ = ('integration_name', 'global_config', 'hooks', 'http')

    def __init__(self, integration_name, global_config, defaults=None):
        defaults = defaults or dict()
        defaults['event_sample_rate'] = IntegrationConfigItem('event_sample_rate')
        super(IntegrationConfig, self).__init__(**defaults)

        attrs = dict(
            integration_name=integration_name,
            global_config=global_config,
            hooks=Hooks(),
            http=HttpConfig(),
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
            val = self[name] = IntegrationConfigItem(name)
        if hasattr(val, '__get__'):
            return val.__get__(self)
        return val

    def __setattr__(self, name, value):
        self[name] = value

    def __getitem__(self, name):
        try:
            value = AttrDict.__getitem__(self, name)
        except KeyError:
            value = IntegrationConfigItem(name)
            self[name] = value
        if hasattr(value, '__get__'):
            return value.__get__(self)
        return value

    def __setitem__(self, name, value):
        try:
            item = AttrDict.__getitem__(self, name)
        except KeyError:
            item = IntegrationConfigItem(name)
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
        # DEV: `dict(self)` will give us the values, calling `self.items()` will give us the `IntegrationConfigItem`s
        new = IntegrationConfig(self.global_config, self.integration_name, deepcopy(dict(self.items())))
        new.hooks = deepcopy(self.hooks)
        new.http = deepcopy(self.http)
        return new

    def copy(self):
        # DEV: `dict(self)` will give us the values, calling `self.items()` will give us the `IntegrationConfigItem`s
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

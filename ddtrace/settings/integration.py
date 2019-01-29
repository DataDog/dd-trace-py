from copy import deepcopy
import os

import six

from ..utils.attrdict import AttrDict
from ..utils.formats import get_env
from ..utils.merge import deepmerge
from .http import HttpConfig
from .hooks import Hooks


class IntegrationConfigItemBase(object):
    """
    Base class that all integration config items should inherit from

    This class should not be used directly, it is only provided as a means to do
    ``isinstance(item, IntegrationConfigItemBase)``
    """
    def _cast(self, value, allow_none=True):
        return value


class IntegrationConfigItem(IntegrationConfigItemBase):
    """
    An integration config item.

    Each ``IntegrationConfigItem`` is meant to be a property on an ``IntegrationConfig`` instance.

    This class follow the ``descriptor`` pattern: https://docs.python.org/2/howto/descriptor.html

    In most cases ``IntegrationConfigItem`` will not be used directly, instead use the aliases on ``Config``:

        @config._register
        class FlaskConfig:
            __integration__ = 'flask'

            service_name = config.Item('service_name')
    """
    __slots__ = ('name', 'value', 'default', 'has_env_var', 'env_override', 'allow_none')

    # Constant to determine if the value of an ``IntegrationConfigItem`` is unset or not
    UNSET = object()

    def __init__(self, name, default=None, doc=None, has_env_var=True, env_override=None, allow_none=True):
        """
        Constructor for ``IntegrationConfigItem``

        Example:

            @config._register
            class FlaskConfig:
                __integration__ = 'flask'

                # Define `config.flask.service_name`
                # Allow overriding via `FLASK_SERVICE_NAME` instead of `DD_FLASK_SERVICE_NAME`
                # Set the default value to 'flask'
                # Do not allow the user to set `None` as a value
                service_name = config.Item(
                    'service_name', doc='Flask app service name', env_override='FLASK_SERVICE_NAME',
                    default='flask', allow_none=False,
                )


        :param name: The name of this item
        :type name: str
        :param default: The default value of this item (default ``None``)
        :type default: any
        :param doc: The doc string to associate with this item
        :type doc: None | str
        :param has_env_var: Whether this item should be associated with an environment variable
        :type has_env_var: bool
        :param env_override: Explicitly set environment variable name (compared to default computed version)
        :type env_override: None | bool
        :param allow_none: Whether ``None`` should be an allowed value for this item (default: ``True``)
        :type allow_none: bool
        """
        self.name = name
        self.value = IntegrationConfigItem.UNSET
        self.default = self._cast(default, allow_none=allow_none)
        self.allow_none = allow_none
        self.env_override = env_override
        # DEV: Force `has_env_var` to be True if `env_override` is set
        self.has_env_var = has_env_var or bool(self.env_override)

        # TOOD: Set a default __doc__
        if doc is not None:
            self.__doc__ = doc

    def alias(self, name, doc=None):
        """
        Create an alias to this item under a different name

        :param name: The name of the alias property
        :type name: str
        :param doc: The doc string for the alias property
        :type doc: None | str
        :returns: A new ``IntergrationConfigItemAlias`` pointing to this item
        :rtype: ``IntegrationConfigItemAlias``
        """
        return IntegrationConfigItemAlias(name=name, alias=self.name, doc=doc)

    def __get__(self, config):
        """
        Descriptor getter for this property, returns the value of this item

        Will return the value based on the following precedence:

          - User supplied value via code (`config.flask.service_name = 'my_service'`)
          - Environment variable value
            - The `env_override` environment variable if supplied, computed `DD_<integration>_<setting>` version if not
          - The default item value

        :param config: The ``IntegrationConfig`` this property is associated with
        :type config: ``IntegrationConfig``
        :returns: The value of this item
        :rtype: any
        """
        if self.value is IntegrationConfigItem.UNSET:
            # Only fetch env variables if we should
            if self.has_env_var:
                # If an environment variable override is set, use that instead
                if self.env_override:
                    return self._cast(os.environ.get(self.env_override, self.default), allow_none=self.allow_none)

                return self._cast(
                    get_env(config.integration_name, self.name, default=self.default),
                    allow_none=self.allow_none,
                )
            else:
                # DEV: We cast `self.default` when setting
                return self.default

        # DEV: We cast `self.value` when setting
        return self.value

    def __set__(self, config, new_value):
        """
        Descriptor setter for this property, will attempt to cast the value

        The supplied new value can be either a value or an ``IntegrationConfigItem``

        If the value is an ``IntegrationConfigItem`` we will only update our default
        value to be that of the passed in item. Unless the default value is a ``dict``
        and then we will merge ``value`` into the ``dict`` and update the value of ``value``.

        Otherwise we attempt to cast and set the value.

        :param config: The ``IntegrationConfig`` this item is attached to
        :type config: ``IntegrationConfig``
        :param new_value: The value to set this item to
        :type new_value: ``IntegrationConfigItem`` | any
        """
        if isinstance(new_value, IntegrationConfigItem):
            self.default = self._cast(new_value.default, allow_none=self.allow_none)

            # If we are updating the default when a `dict` value is already set
            # then merge the value and default dict together
            if isinstance(self.value, dict) and isinstance(self.default, dict):
                d = deepcopy(self.default)
                self.value = deepmerge(self.value, d)
        else:
            if isinstance(new_value, dict) and isinstance(self.default, dict):
                d = deepcopy(self.default)
                new_value = deepmerge(new_value, d)
            self.value = self._cast(new_value, allow_none=self.allow_none)

    def __repr__(self):
        """String representation of this instance"""
        value = self.value if self.value is not IntegrationConfigItem.UNSET else self.default

        return '{0}(name={1!r}, value={2!r})'.format(
            self.__class__.__name__,
            self.name,
            value,
        )

    def __deepcopy__(self, memodict=None):
        """Helper for ``copy.deepcopy``"""
        return self.copy()

    def copy(self):
        """
        Create a copy of this ``IntegrationConfigItem``

        :returns: A new ``IntegrationConfigItem`` with no references to this items values
        :rtype: ``IntegrationConfigItem``
        """
        c = IntegrationConfigItem(
            self.name,
            default=deepcopy(self.default),
            doc=self.__doc__,
            has_env_var=self.has_env_var,
            env_override=self.env_override,
            allow_none=self.allow_none,
        )

        if self.value is not IntegrationConfigItem.UNSET:
            # DEV: This cast probably isn't necessary, but doesn't hurt
            c.value = self._cast(deepcopy(self.value), allow_none=self.allow_none)
        return c


class IntegrationConfigItemAlias(IntegrationConfigItemBase):
    """
    An alias to an existing ``IntegrationConfigItem``.

    This item is used when you need to migrate a configuration name and want backwards compatibility.

    This alias will pass through all get and set operations to the aliased configuration item.

    Example:

        @config._register
        class FlaskConfig:
            __integration__ = 'flask'

            distributed_tracing = config.Bool('distributed_tracing')
            # Create an alias to `distributed_tracing`
            distributed_tracing_enabled = distributed_tracing.alias('distributed_tracing')
    """
    __slots__ = ('name', 'alias')

    def __init__(self, name, alias, doc=None):
        """
        Constructor for ``IntegrationConfigItemAlias``

        :param name: The name of this property
        :type name: str
        :param alias: The name of the property to alias
        :type alias: str
        :param doc: The doc string to associate with this property
        :type doc: None | str
        """
        self.name = name
        self.alias = alias

        # TODO: Set a default doc string
        if doc is not None:
            self.__doc__ = doc

    def __get__(self, config):
        """
        Descriptor getter for this item

        This getter will take the following precedence for retrieving it's value:

          - The value set the by user on the aliased item
          - The environment variable associated with this alias
            - DD_<integration>_<property>
          - The value determined by the aliased items ``__get__`` function

        :param config: The ``IntegrationConfig`` this item is attached to
        :type config: ``IntegrationConfig``
        :returns: The value of this item
        :rtype: any
        """
        # Get the aliased item
        parent_item = config.get_item(self.alias)

        # If the alias does not exist, return the value of the environment variable for this item
        if not parent_item:
            return self._cast(get_env(config.integration_name, self.name))

        # If a user defined value has been set on the aliased item, return that
        if parent_item.value is not IntegrationConfigItem.UNSET:
            return parent_item.value

        # Otherwise return:
        #   - The environment variable for this item
        #   - The result of the parent item computing the value
        # Cast the value using the parennt items `_cast()` function
        return parent_item._cast(get_env(config.integration_name, self.name, default=parent_item.__get__(config)))

    def __set__(self, config, value):
        """
        Descriptor setter for this item

        This setter is just a pass through to the aliased properties ``__set__`` method.

        :param config: The ``IntegrationConfig`` this item is attached to
        :type config: ``IntegrationConfig``
        :param value: The value to set this item to
        :type value: ``IntegrationConfigItem`` | any
        """
        item = config.get_item(self.alias)
        if item:
            item.__set__(config, value)

    def __repr__(self):
        """String representation of this item"""
        return '{0}(name={1!r}, alias={2!r})'.format(
            self.__class__.__name__,
            self.name,
            self.alias,
        )

    def __deepcopy__(self, memodict=None):
        """Helper for ``copy.deepcopy()``"""
        return self.copy()

    def copy(self):
        """
        Create a copy of this ``IntegrationConfigItemAlias``

        :returns: A copy of this ``IntegrationConfigItemAlias``
        :rtype: ``IntegrationConfigItemAlias``
        """
        return IntegrationConfigItemAlias(self.name, self.alias, doc=self.__doc__)


class BoolIntegrationConfigItem(IntegrationConfigItem):
    """
    Boolean ``IntegrationConfigItem``.

    This class will attempt to parse and cast it's value to a ``bool``.

    Example:

        @config._register
        class FlaskConfig:
            __integration__ = 'flask'

            is_enabled = config.Bool('is_enabled', default=True)
    """
    def _cast(self, value, allow_none=True):
        """
        Casting function to coerce if ``value`` to a boolean

        If ``value`` is a string we evaluate 'true' and '1' as ``True`` everything else is ``False``.

        Otherwise, we cast values as ``bool(value)``.

        :param value: The value to cast to a ``bool``
        :type value: any
        :param allow_none: Whether to allow the value to be ``None`` (default: ``True``)
        :type allow_none: bool
        :returns: The casted ``value``
        :rtype: bool | None
        """
        if value is None:
            return None if allow_none else False
        elif isinstance(value, bool):
            return value
        elif isinstance(value, six.string_types):
            return value.strip().lower() in ('true', '1')
        return bool(value)


class FloatIntegrationConfigItem(IntegrationConfigItem):
    """
    Float ``IntegrationConfigItem``.

    This class will attempt to parse and cast it's value to a ``float``.

    Example:

        @config._register
        class FlaskConfig:
            __integration__ = 'flask'

            event_sample_rate = config.Float('event_sample_rate', default=1.0)
    """
    def _cast(self, value, allow_none=True):
        """
        Casting function to coerce if ``value`` to a float

        We cast values as ``float(value)``.

        :param value: The value to cast to a ``float``
        :type value: any
        :param allow_none: Whether to allow the value to be ``None`` (default: ``True``)
        :type allow_none: bool
        :returns: The casted ``value``
        :rtype: float | None
        """
        if value is None:
            return None if allow_none else 0.0
        elif isinstance(value, float):
            return value
        elif isinstance(value, six.string_types):
            return float(value.strip())
        return float(value)


class IntIntegrationConfigItem(IntegrationConfigItem):
    """
    Integer ``IntegrationConfigItem``.

    This class will attempt to parse and cast it's value to an ``int``.

    Example:

        @config._register
        class FlaskConfig:
            __integration__ = 'flask'

            default_response_code = config.Int('default_response_code', default=200)
    """
    def _cast(self, value, allow_none=True):
        """
        Casting function to coerce if ``value`` to an integer

        We cast values as ``int(value)``.

        :param value: The value to cast to a ``int``
        :type value: any
        :param allow_none: Whether to allow the value to be ``None`` (default: ``True``)
        :type allow_none: bool
        :returns: The casted ``value``
        :rtype: int | None
        """
        if value is None:
            return None if allow_none else 0
        elif isinstance(value, int):
            return value
        elif isinstance(value, six.string_types):
            return int(value.strip())
        return int(value)


class IntegrationConfig(AttrDict):
    """
    Configuration object for an integration

    Example:

        from ddtrace import config, tracer

        @config._register
        class FlaskConfig:
            __integration__ = 'flask'

            service_name = config.Item('service_name', default='flask')
            distributed_tracing = config.Bool('distributed_tracing', default=True)


        with tracer.trace('flask.request', service=config.flask.service_name):
            pass
    """
    __slots__ = ('integration_name', 'global_config', 'hooks', 'http')

    def __init__(self, global_config, integration_name, defaults=None):
        defaults = defaults or dict()
        defaults['event_sample_rate'] = FloatIntegrationConfigItem('event_sample_rate')
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
        if values is not None:
            if hasattr(values, 'items'):
                values = values.items()

            for k, v in values:
                self[k] = v

        for k, v in kwargs.items():
            self[k] = v

    def get(self, name, default=None):
        """
        Get the value of a specific config item or else the default value

        :param name: The name of the config item to fetch
        :type name: str
        :param default: The default value to return if no item was found (default: ``None``)
        :type default: any
        :returns: The value of the config item found or else the ``default``
        :rtype: any
        """
        item = self.get_item(name)
        if item:
            return item.__get__(self)
        return default

    def get_item(self, name):
        """
        Attempt to fetch an ``IntegrationConfigItem`` from this instance.

        :param name: The name of the ``IntegrationConfigItem`` to get
        :type name: str
        :returns: The found ``IntegrationConfigItem`` or ``None``
        :rtype: IntegrationConfigItem | None
        """
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
        return self.copy()

    def copy(self):
        # DEV: `dict(self)` will give us the values, calling `self.items()` will give us the `IntegrationConfigItem`s
        new = IntegrationConfig(
            self.global_config,
            self.integration_name,
            defaults=dict([(name, deepcopy(item)) for name, item in self.items()]),
        )
        attrs = dict(
            hooks=deepcopy(self.hooks),
            http=deepcopy(self.http),
        )
        for attr, value in attrs.items():
            object.__setattr__(new, attr, value)
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

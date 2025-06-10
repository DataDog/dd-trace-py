from typing import TYPE_CHECKING  # noqa:F401
from typing import Any  # noqa:F401
from typing import Dict  # noqa:F401
from typing import Optional  # noqa:F401

import wrapt

import ddtrace
from ddtrace.settings.asm import config as asm_config

from ..internal.logger import get_logger


log = get_logger(__name__)


# To set attributes on wrapt proxy objects use this prefix:
# http://wrapt.readthedocs.io/en/latest/wrappers.html
_DD_PIN_NAME = "_datadog_pin"
_DD_PIN_PROXY_NAME = "_self_" + _DD_PIN_NAME


class Pin(object):
    """Pin (a.k.a Patch INfo) is a small class which is used to
    set tracing metadata on a particular traced connection.
    This is useful if you wanted to, say, trace two different
    database clusters.

        >>> conn = sqlite.connect('/tmp/user.db')
        >>> # Override a pin for a specific connection
        >>> pin = Pin.override(conn, service='user-db')
        >>> conn = sqlite.connect('/tmp/image.db')
    """

    __slots__ = ["tags", "_tracer", "_target", "_config", "_initialized"]

    def __init__(
        self,
        service=None,  # type: Optional[str]
        tags=None,  # type: Optional[Dict[str, str]]
        _config=None,  # type: Optional[Dict[str, Any]]
    ):
        # type: (...) -> None
        self.tags = tags
        self._tracer = ddtrace.tracer
        self._target = None  # type: Optional[int]
        # keep the configuration attribute internal because the
        # public API to access it is not the Pin class
        self._config = _config or {}  # type: Dict[str, Any]
        # [Backward compatibility]: service argument updates the `Pin` config
        self._config["service_name"] = service
        self._initialized = True

    @property
    def service(self):
        # type: () -> str
        """Backward compatibility: accessing to `pin.service` returns the underlying
        configuration value.
        """
        return self._config["service_name"]

    def __setattr__(self, name, value):
        if getattr(self, "_initialized", False) and name not in ("_target", "_tracer"):
            raise AttributeError("can't mutate a pin, use override() or clone() instead")
        super(Pin, self).__setattr__(name, value)

    @property
    def tracer(self):
        return self._tracer

    def __repr__(self):
        return "Pin(service=%s, tags=%s, tracer=%s)" % (self.service, self.tags, self.tracer)

    @staticmethod
    def _find(*objs):
        # type: (Any) -> Optional[Pin]
        """
        Return the first :class:`ddtrace.trace.Pin` found on any of the provided objects or `None` if none were found


            >>> pin = Pin._find(wrapper, instance, conn)

        :param objs: The objects to search for a :class:`ddtrace.trace.Pin` on
        :type objs: List of objects
        :rtype: :class:`ddtrace.trace.Pin`, None
        :returns: The first found :class:`ddtrace.trace.Pin` or `None` is none was found
        """
        for obj in objs:
            pin = Pin.get_from(obj)
            if pin:
                return pin
        return None

    @staticmethod
    def get_from(obj):
        # type: (Any) -> Optional[Pin]
        """Return the pin associated with the given object. If a pin is attached to
        `obj` but the instance is not the owner of the pin, a new pin is cloned and
        attached. This ensures that a pin inherited from a class is a copy for the new
        instance, avoiding that a specific instance overrides other pins values.

            >>> pin = Pin.get_from(conn)

        :param obj: The object to look for a :class:`ddtrace.trace.Pin` on
        :type obj: object
        :rtype: :class:`ddtrace.trace.Pin`, None
        :returns: :class:`ddtrace.trace.Pin` associated with the object, or None if none was found
        """
        if hasattr(obj, "__getddpin__"):
            return obj.__getddpin__()

        pin_name = _DD_PIN_PROXY_NAME if isinstance(obj, wrapt.ObjectProxy) else _DD_PIN_NAME
        pin = getattr(obj, pin_name, None)
        # detect if the PIN has been inherited from a class
        if pin is not None and pin._target != id(obj):
            pin = pin.clone()
            pin.onto(obj)
        return pin

    @staticmethod
    def _get_config(obj: Any) -> Dict[str, Any]:
        """Retrieves the configuration for the given object.
        Any object that has an attached `Pin` must have a configuration
        and if a wrong object is given, an empty `dict` is returned
        for safety reasons.
        """
        pin = Pin.get_from(obj)
        if pin is None:
            log.debug("No configuration found for %s", obj)
            return {}

        return pin._config

    @classmethod
    def override(
        cls,
        obj,  # type: Any
        service=None,  # type: Optional[str]
        tags=None,  # type: Optional[Dict[str, str]]
    ):
        # type: (...) -> None
        """Override an object with the given attributes.

        That's the recommended way to customize an already instrumented client, without
        losing existing attributes.

            >>> conn = sqlite.connect('/tmp/user.db')
            >>> # Override a pin for a specific connection
            >>> Pin.override(conn, service='user-db')
        """
        Pin._override(obj, service=service, tags=tags)

    @classmethod
    def _override(
        cls,
        obj,  # type: Any
        service=None,  # type: Optional[str]
        tags=None,  # type: Optional[Dict[str, str]]
        tracer=None,
    ):
        # type: (...) -> None
        """
        Internal method that allows overriding the global tracer in tests
        """
        if not obj:
            return

        pin = cls.get_from(obj)
        if pin is None:
            pin = Pin(service=service, tags=tags)
        else:
            pin = pin.clone(service=service, tags=tags)

        if tracer:
            pin._tracer = tracer
        pin.onto(obj)

    def enabled(self) -> bool:
        """Return true if this pin's tracer is enabled."""
        return bool(self.tracer) and (self.tracer.enabled or asm_config._apm_opt_out)

    def onto(self, obj, send=True):
        # type: (Any, bool) -> None
        """Patch this pin onto the given object. If send is true, it will also
        queue the metadata to be sent to the server.
        """
        # Actually patch it on the object.
        try:
            if hasattr(obj, "__setddpin__"):
                return obj.__setddpin__(self)

            pin_name = _DD_PIN_PROXY_NAME if isinstance(obj, wrapt.ObjectProxy) else _DD_PIN_NAME

            # set the target reference; any get_from, clones and retarget the new PIN
            self._target = id(obj)
            if self.service:
                ddtrace.config._add_extra_service(self.service)
            return setattr(obj, pin_name, self)
        except AttributeError:
            log.debug("can't pin onto object. skipping", exc_info=True)

    def remove_from(self, obj):
        # type: (Any) -> None
        # Remove pin from the object.
        try:
            pin_name = _DD_PIN_PROXY_NAME if isinstance(obj, wrapt.ObjectProxy) else _DD_PIN_NAME

            pin = Pin.get_from(obj)
            if pin is not None:
                delattr(obj, pin_name)
        except AttributeError:
            log.debug("can't remove pin from object. skipping", exc_info=True)

    def clone(
        self,
        service=None,  # type: Optional[str]
        tags=None,  # type: Optional[Dict[str, str]]
    ):
        # type: (...) -> Pin
        """Return a clone of the pin with the given attributes replaced."""
        return self._clone(service=service, tags=tags)

    def _clone(
        self,
        service=None,  # type: Optional[str]
        tags=None,  # type: Optional[Dict[str, str]]
        tracer=None,
    ):
        """Internal method that can clone the tracer from an existing Pin. This is used in tests"""
        # do a shallow copy of Pin dicts
        if not tags and self.tags:
            tags = self.tags.copy()

        # we use a copy instead of a deepcopy because we expect configurations
        # to have only a root level dictionary without nested objects. Using
        # deepcopy introduces a big overhead:
        #
        # copy: 0.00654911994934082
        # deepcopy: 0.2787208557128906
        config = self._config.copy()

        pin = Pin(
            service=service or self.service,
            tags=tags,
            _config=config,
        )
        pin._tracer = tracer or self.tracer
        return pin

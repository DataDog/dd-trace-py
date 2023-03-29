import ctypes
import ctypes.util
from enum import IntEnum
import os
from platform import machine
from platform import system
from typing import TYPE_CHECKING

from ddtrace.internal.compat import PY3
from ddtrace.internal.compat import text_type as unicode
from ddtrace.internal.logger import get_logger


if TYPE_CHECKING:
    from typing import Any
    from typing import Optional
    from typing import Union

    DDWafRulesType = Union[None, int, unicode, list[Any], dict[unicode, Any]]

if PY3:
    long = int

_DIRNAME = os.path.dirname(__file__)

FILE_EXTENSION = {"Linux": "so", "Darwin": "dylib", "Windows": "dll"}[system()]


log = get_logger(__name__)

#
# Dynamic loading of libddwaf. For now it requires the file or a link to be in current directory
#

if system() == "Linux":
    ctypes.CDLL(ctypes.util.find_library("rt"), mode=ctypes.RTLD_GLOBAL)

ARCHI = machine().lower()

# 32-bit-Python on 64-bit-Windows
if system() == "Windows" and ARCHI == "amd64":
    from sys import maxsize

    if maxsize <= (1 << 32):
        ARCHI = "x86"

TRANSLATE_ARCH = {"amd64": "x64", "i686": "x86_64", "x86": "win32"}
ARCHITECTURE = TRANSLATE_ARCH.get(ARCHI, ARCHI)

ddwaf = ctypes.CDLL(os.path.join(_DIRNAME, "libddwaf", ARCHITECTURE, "lib", "libddwaf." + FILE_EXTENSION))
#
# Constants
#

DDWAF_MAX_STRING_LENGTH = 4096
DDWAF_MAX_CONTAINER_DEPTH = 20
DDWAF_MAX_CONTAINER_SIZE = 256
DDWAF_NO_LIMIT = 1 << 31
DDWAF_DEPTH_NO_LIMIT = 1000


class DDWAF_OBJ_TYPE(IntEnum):
    DDWAF_OBJ_INVALID = 0
    # Value shall be decoded as a int64_t (or int32_t on 32bits platforms).
    DDWAF_OBJ_SIGNED = 1 << 0
    # Value shall be decoded as a uint64_t (or uint32_t on 32bits platforms).
    DDWAF_OBJ_UNSIGNED = 1 << 1
    # Value shall be decoded as a UTF-8 string of length nbEntries.
    DDWAF_OBJ_STRING = 1 << 2
    # Value shall be decoded as an array of ddwaf_object of length nbEntries, each item having no parameterName.
    DDWAF_OBJ_ARRAY = 1 << 3
    # Value shall be decoded as an array of ddwaf_object of length nbEntries, each item having a parameterName.
    DDWAF_OBJ_MAP = 1 << 4
    # Value shall be decode as bool
    DDWAF_OBJ_BOOL = 1 << 5


class DDWAF_RET_CODE(IntEnum):
    DDWAF_ERR_INTERNAL = -3
    DDWAF_ERR_INVALID_OBJECT = -2
    DDWAF_ERR_INVALID_ARGUMENT = -1
    DDWAF_OK = 0
    DDWAF_MATCH = 1


class DDWAF_LOG_LEVEL(IntEnum):
    DDWAF_LOG_TRACE = 0
    DDWAF_LOG_DEBUG = 1
    DDWAF_LOG_INFO = 2
    DDWAF_LOG_WARN = 3
    DDWAF_LOG_ERROR = 4
    DDWAF_LOG_OFF = 5


#
# Objects Definitions
#

# obj_struct = DDWafRulesType


# to allow cyclic references, ddwaf_object fields are defined later
class ddwaf_object(ctypes.Structure):
    # "type" define how to read the "value" union field
    # defined in ddwaf.h
    #  1 is intValue
    #  2 is uintValue
    #  4 is stringValue as UTF-8 encoded
    #  8 is array of length "nbEntries" without parameterName
    # 16 is a map : array of length "nbEntries" with parameterName
    # 32 is boolean

    def __init__(
        self,
        struct=None,
        max_objects=DDWAF_MAX_CONTAINER_SIZE,
        max_depth=DDWAF_MAX_CONTAINER_DEPTH,
        max_string_length=DDWAF_MAX_STRING_LENGTH,
    ):
        # type: (DDWafRulesType, int, int, int) -> None
        if isinstance(struct, bool):
            ddwaf_object_bool(self, struct)
        elif isinstance(struct, (int, long)):
            ddwaf_object_signed(self, struct)
        elif isinstance(struct, unicode):
            ddwaf_object_string(self, struct.encode("UTF-8", errors="ignore")[: max_string_length - 1])
        elif isinstance(struct, bytes):
            ddwaf_object_string(self, struct[: max_string_length - 1])
        elif isinstance(struct, float):
            res = unicode(struct).encode("UTF-8", errors="ignore")[: max_string_length - 1]
            ddwaf_object_string(self, res)
        elif isinstance(struct, list):
            if max_depth <= 0:
                max_objects = 0
            array = ddwaf_object_array(self)
            for counter_object, elt in enumerate(struct):
                if counter_object >= max_objects:
                    break
                obj = ddwaf_object(
                    elt, max_objects=max_objects, max_depth=max_depth - 1, max_string_length=max_string_length
                )
                if obj.type:  # discards invalid objects
                    ddwaf_object_array_add(array, obj)
        elif isinstance(struct, dict):
            if max_depth <= 0:
                max_objects = 0
            map_o = ddwaf_object_map(self)
            # order is unspecified and could lead to problems if max_objects is reached
            for counter_object, (key, val) in enumerate(struct.items()):
                if not isinstance(key, (bytes, unicode)):  # discards non string keys
                    continue
                if counter_object >= max_objects:
                    break
                res_key = (key.encode("UTF-8", errors="ignore") if isinstance(key, unicode) else key)[
                    : max_string_length - 1
                ]
                obj = ddwaf_object(
                    val, max_objects=max_objects, max_depth=max_depth - 1, max_string_length=max_string_length
                )
                if obj.type:  # discards invalid objects
                    ddwaf_object_map_add(map_o, res_key, obj)
        elif struct is not None:
            struct = str(struct)
            if isinstance(struct, bytes):  # Python 2
                ddwaf_object_string(self, struct[: max_string_length - 1])
            else:  # Python 3
                ddwaf_object_string(self, struct.encode("UTF-8", errors="ignore")[: max_string_length - 1])
        else:
            ddwaf_object_invalid(self)

    @classmethod
    def create_without_limits(cls, struct):
        # type: (type, DDWafRulesType) -> ddwaf_object
        return cls(struct, DDWAF_NO_LIMIT, DDWAF_DEPTH_NO_LIMIT, DDWAF_NO_LIMIT)

    @property
    def struct(self):
        # type: (ddwaf_object) -> Union[None, int, unicode, list[Any], dict[unicode, Any]]
        """pretty printing of the python ddwaf_object"""
        if self.type == DDWAF_OBJ_TYPE.DDWAF_OBJ_INVALID:
            return None
        if self.type == DDWAF_OBJ_TYPE.DDWAF_OBJ_SIGNED:
            return self.value.intValue
        if self.type == DDWAF_OBJ_TYPE.DDWAF_OBJ_UNSIGNED:
            return self.value.uintValue
        if self.type == DDWAF_OBJ_TYPE.DDWAF_OBJ_STRING:
            return self.value.stringValue.decode("UTF-8", errors="ignore")
        if self.type == DDWAF_OBJ_TYPE.DDWAF_OBJ_ARRAY:
            return [self.value.array[i].struct for i in range(self.nbEntries)]
        if self.type == DDWAF_OBJ_TYPE.DDWAF_OBJ_MAP:
            return {
                self.value.array[i].parameterName.decode("UTF-8", errors="ignore"): self.value.array[i].struct
                for i in range(self.nbEntries)
            }
        if self.type == DDWAF_OBJ_TYPE.DDWAF_OBJ_BOOL:
            return self.value.boolean
        log.debug("ddwaf_object struct: unknown object type: %s", repr(type(self.type)))
        return None

    def __repr__(self):
        return repr(self.struct)


ddwaf_object_p = ctypes.POINTER(ddwaf_object)


class ddwaf_value(ctypes.Union):
    _fields_ = [
        ("stringValue", ctypes.c_char_p),
        ("uintValue", ctypes.c_ulonglong),
        ("intValue", ctypes.c_longlong),
        ("array", ddwaf_object_p),
        ("boolean", ctypes.c_bool),
    ]


ddwaf_object._fields_ = [
    ("parameterName", ctypes.c_char_p),
    ("parameterNameLength", ctypes.c_uint64),
    ("value", ddwaf_value),
    ("nbEntries", ctypes.c_uint64),
    ("type", ctypes.c_int),
]


class ddwaf_result_action(ctypes.Structure):
    _fields_ = [
        ("array", ctypes.POINTER(ctypes.c_char_p)),
        ("size", ctypes.c_uint32),
    ]

    def __repr__(self):
        return ", ".join(self.array[i] for i in range(self.size))


class ddwaf_result(ctypes.Structure):
    _fields_ = [
        ("timeout", ctypes.c_bool),
        ("data", ctypes.c_char_p),
        ("actions", ddwaf_result_action),
        ("total_runtime", ctypes.c_uint64),
    ]

    def __repr__(self):
        return "total_runtime=%r, data=%r, timeout=%r, action=[%r]" % (
            self.total_runtime,
            self.data,
            self.timeout,
            self.actions,
        )

    def __del__(self):
        try:
            ddwaf_result_free(self)
        except TypeError:
            pass


ddwaf_result_p = ctypes.POINTER(ddwaf_result)


class ddwaf_ruleset_info(ctypes.Structure):
    _fields_ = [
        ("loaded", ctypes.c_uint16),
        ("failed", ctypes.c_uint16),
        ("errors", ddwaf_object),
        ("version", ctypes.c_char_p),
    ]


ddwaf_ruleset_info_p = ctypes.POINTER(ddwaf_ruleset_info)


class ddwaf_config_limits(ctypes.Structure):
    _fields_ = [
        ("max_container_size", ctypes.c_uint32),
        ("max_container_depth", ctypes.c_uint32),
        ("max_string_length", ctypes.c_uint32),
    ]


class ddwaf_config_obfuscator(ctypes.Structure):
    _fields_ = [
        ("key_regex", ctypes.c_char_p),
        ("value_regex", ctypes.c_char_p),
    ]


ddwaf_object_free_fn = ctypes.CFUNCTYPE(None, ddwaf_object_p)
ddwaf_object_free = ddwaf_object_free_fn(
    ("ddwaf_object_free", ddwaf),
    ((1, "object"),),
)


class ddwaf_config(ctypes.Structure):
    _fields_ = [
        ("limits", ddwaf_config_limits),
        ("obfuscator", ddwaf_config_obfuscator),
        ("free_fn", ddwaf_object_free_fn),
    ]
    # TODO : initial value of free_fn

    def __init__(
        self,
        max_container_size=0,
        max_container_depth=0,
        max_string_length=0,
        key_regex="",
        value_regex="",
        free_fn=ddwaf_object_free,
    ):
        # type: (ddwaf_config, int, int, int, unicode, unicode, Optional[Any]) -> None
        self.limits.max_container_size = max_container_size
        self.limits.max_container_depth = max_container_depth
        self.limits.max_string_length = max_string_length
        self.obfuscator.key_regex = key_regex
        self.obfuscator.value_regex = value_regex
        self.free_fn = free_fn


ddwaf_config_p = ctypes.POINTER(ddwaf_config)


ddwaf_handle = ctypes.c_void_p  # may stay as this because it's mainly an abstract type in the interface
ddwaf_context = ctypes.c_void_p  # may stay as this because it's mainly an abstract type in the interface


class ddwaf_handle_capsule:
    def __init__(self, handle):
        # type: (ddwaf_handle) -> None
        self.handle = handle
        self.free_fn = ddwaf_destroy

    def __del__(self):
        if self.handle:
            try:
                self.free_fn(self.handle)
            except TypeError:
                pass
            self.handle = None

    def __bool__(self):
        return bool(self.handle)


class ddwaf_context_capsule:
    def __init__(self, ctx):
        # type: (ddwaf_context) -> None
        self.ctx = ctx
        self.free_fn = ddwaf_context_destroy

    def __del__(self):
        if self.ctx:
            try:
                self.free_fn(self.ctx)
            except TypeError:
                pass
            self.ctx = None

    def __bool__(self):
        return bool(self.ctx)


ddwaf_log_cb = ctypes.POINTER(
    ctypes.CFUNCTYPE(
        None, ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint, ctypes.c_char_p, ctypes.c_uint64
    )
)


#
# Functions Prototypes (creating python counterpart function from C function with )
#

ddwaf_init = ctypes.CFUNCTYPE(ddwaf_handle, ddwaf_object_p, ddwaf_config_p, ddwaf_ruleset_info_p)(
    ("ddwaf_init", ddwaf),
    (
        (1, "ruleset_map"),
        (1, "config", None),
        (1, "info", None),
    ),
)


def py_ddwaf_init(ruleset_map, config, info):
    # type: (ddwaf_object, Any, Any) -> ddwaf_handle_capsule
    return ddwaf_handle_capsule(ddwaf_init(ruleset_map, config, info))


ddwaf_update = ctypes.CFUNCTYPE(ddwaf_handle, ddwaf_handle, ddwaf_object_p, ddwaf_ruleset_info_p)(
    ("ddwaf_update", ddwaf),
    (
        (1, "handle"),
        (1, "ruleset_map"),
        (1, "info", None),
    ),
)


def py_ddwaf_update(handle, ruleset_map, info):
    # type: (ddwaf_handle_capsule, ddwaf_object, Any) -> ddwaf_handle_capsule
    return ddwaf_handle_capsule(ddwaf_update(handle.handle, ruleset_map, ctypes.byref(info)))


ddwaf_destroy = ctypes.CFUNCTYPE(None, ddwaf_handle)(
    ("ddwaf_destroy", ddwaf),
    ((1, "handle"),),
)


ddwaf_ruleset_info_free = ctypes.CFUNCTYPE(None, ddwaf_ruleset_info_p)(
    ("ddwaf_ruleset_info_free", ddwaf),
    ((1, "info"),),
)

ddwaf_required_addresses = ctypes.CFUNCTYPE(
    ctypes.POINTER(ctypes.c_char_p), ddwaf_handle, ctypes.POINTER(ctypes.c_uint32)
)(
    ("ddwaf_required_addresses", ddwaf),
    (
        (1, "handle"),
        (1, "size"),
    ),
)


def py_ddwaf_required_addresses(handle):
    # type: (ddwaf_handle_capsule) -> list[unicode]
    size = ctypes.c_uint32()
    obj = ddwaf_required_addresses(handle.handle, ctypes.byref(size))
    return [obj[i].decode("UTF-8") for i in range(size.value)]


ddwaf_context_init = ctypes.CFUNCTYPE(ddwaf_context, ddwaf_handle)(
    ("ddwaf_context_init", ddwaf),
    ((1, "handle"),),
)


def py_ddwaf_context_init(handle):
    # type: (ddwaf_handle_capsule) -> ddwaf_context_capsule
    return ddwaf_context_capsule(ddwaf_context_init(handle.handle))


ddwaf_run = ctypes.CFUNCTYPE(ctypes.c_int, ddwaf_context, ddwaf_object_p, ddwaf_result_p, ctypes.c_uint64)(
    ("ddwaf_run", ddwaf), ((1, "context"), (1, "data"), (1, "result"), (1, "timeout"))
)

ddwaf_context_destroy = ctypes.CFUNCTYPE(None, ddwaf_context)(
    ("ddwaf_context_destroy", ddwaf),
    ((1, "context"),),
)

ddwaf_result_free = ctypes.CFUNCTYPE(None, ddwaf_result_p)(
    ("ddwaf_result_free", ddwaf),
    ((1, "result"),),
)

ddwaf_object_invalid = ctypes.CFUNCTYPE(ddwaf_object_p, ddwaf_object_p)(
    ("ddwaf_object_invalid", ddwaf),
    ((3, "object"),),
)

ddwaf_object_string = ctypes.CFUNCTYPE(ddwaf_object_p, ddwaf_object_p, ctypes.c_char_p)(
    ("ddwaf_object_string", ddwaf),
    (
        (3, "object"),
        (1, "string"),
    ),
)

# object_string variants not used

ddwaf_object_unsigned = ctypes.CFUNCTYPE(ddwaf_object_p, ddwaf_object_p, ctypes.c_uint64)(
    ("ddwaf_object_unsigned", ddwaf),
    (
        (3, "object"),
        (1, "value"),
    ),
)

ddwaf_object_signed = ctypes.CFUNCTYPE(ddwaf_object_p, ddwaf_object_p, ctypes.c_int64)(
    ("ddwaf_object_signed", ddwaf),
    (
        (3, "object"),
        (1, "value"),
    ),
)

# object_(un)signed_forced : not used ?

ddwaf_object_bool = ctypes.CFUNCTYPE(ddwaf_object_p, ddwaf_object_p, ctypes.c_bool)(
    ("ddwaf_object_bool", ddwaf),
    (
        (3, "object"),
        (1, "value"),
    ),
)

ddwaf_object_array = ctypes.CFUNCTYPE(ddwaf_object_p, ddwaf_object_p)(
    ("ddwaf_object_array", ddwaf),
    ((3, "object"),),
)

ddwaf_object_map = ctypes.CFUNCTYPE(ddwaf_object_p, ddwaf_object_p)(
    ("ddwaf_object_map", ddwaf),
    ((3, "object"),),
)

ddwaf_object_array_add = ctypes.CFUNCTYPE(ctypes.c_bool, ddwaf_object_p, ddwaf_object_p)(
    ("ddwaf_object_array_add", ddwaf),
    (
        (1, "array"),
        (1, "object"),
    ),
)

ddwaf_object_map_add = ctypes.CFUNCTYPE(ctypes.c_bool, ddwaf_object_p, ctypes.c_char_p, ddwaf_object_p)(
    ("ddwaf_object_map_add", ddwaf),
    (
        (1, "map"),
        (1, "key"),
        (1, "object"),
    ),
)

# unused because accessible from python part
# ddwaf_object_type
# ddwaf_object_size
# ddwaf_object_length
# ddwaf_object_get_key
# ddwaf_object_get_string
# ddwaf_object_get_unsigned
# ddwaf_object_get_signed
# ddwaf_object_get_index
# ddwaf_object_get_bool https://github.com/DataDog/libddwaf/commit/7dc68dacd972ae2e2a3c03a69116909c98dbd9cb


ddwaf_get_version = ctypes.CFUNCTYPE(ctypes.c_char_p)(
    ("ddwaf_get_version", ddwaf),
    (),
)


ddwaf_set_log_cb = ctypes.CFUNCTYPE(ctypes.c_bool, ddwaf_log_cb, ctypes.c_int)(
    ("ddwaf_set_log_cb", ddwaf),
    (
        (1, "cb"),
        (1, "min_level"),
    ),
)

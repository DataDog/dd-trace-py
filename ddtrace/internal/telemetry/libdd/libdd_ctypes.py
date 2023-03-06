from ctypes import POINTER
from ctypes import Structure
from ctypes import Union
from ctypes import c_bool
from ctypes import c_char
from ctypes import c_char_p
from ctypes import c_size_t
from ctypes import c_ubyte
from ctypes import c_uint32
from ctypes import c_uint64
from ctypes import cast


class Builder(Structure):
    pass


class Handler(Structure):
    pass


class CharSlice(Structure):
    _fields_ = [("ptr", c_char_p), ("len", c_size_t)]


# values for enumeration 'ddog_LogLevel'
ddog_LogLevel__enumvalues = {
    0: "DDOG_LOG_LEVEL_ERROR",
    1: "DDOG_LOG_LEVEL_WARN",
    2: "DDOG_LOG_LEVEL_DEBUG",
}
DDOG_LOG_LEVEL_ERROR = 0
DDOG_LOG_LEVEL_WARN = 1
DDOG_LOG_LEVEL_DEBUG = 2
ddog_LogLevel = c_uint32  # enum

# ------------------------------------- #


# Define Ddog Optional Bool
ddog_Option_Bool_Tag__enumvalues = {
    0: "DDOG_OPTION_BOOL_SOME_BOOL",
    1: "DDOG_OPTION_BOOL_NONE_BOOL",
}
DDOG_OPTION_BOOL_SOME_BOOL = 0
DDOG_OPTION_BOOL_NONE_BOOL = 1
ddog_Option_Bool_Tag = c_uint32  # enum


class struct_ddog_Option_Bool(Structure):
    pass


class union_ddog_Option_Bool_0(Union):
    pass


class struct_ddog_Option_Bool_0_0(Structure):
    pass


struct_ddog_Option_Bool_0_0._pack_ = 1  # source:False
struct_ddog_Option_Bool_0_0._fields_ = [
    ("some", c_bool),
]
union_ddog_Option_Bool_0._pack_ = 1  # source:False
union_ddog_Option_Bool_0._fields_ = [
    ("ddog_Option_Bool_0_0", struct_ddog_Option_Bool_0_0),
]
struct_ddog_Option_Bool._pack_ = 1  # source:False
struct_ddog_Option_Bool._fields_ = [
    ("tag", ddog_Option_Bool_Tag),
    ("ddog_Option_Bool_0", union_ddog_Option_Bool_0),
    ("PADDING_0", c_ubyte * 3),
]
ddog_Option_Bool = struct_ddog_Option_Bool

# ------------------------------------- #


# ddog maybe type
class AsDictMixin:
    @classmethod
    def as_dict(cls, self):
        result = {}
        if not isinstance(self, AsDictMixin):
            # not a structure, assume it's already a python object
            return self
        if not hasattr(cls, "_fields_"):
            return result
        # sys.version_info >= (3, 5)
        # for (field, *_) in cls._fields_:  # noqa
        for field_tuple in cls._fields_:  # noqa
            field = field_tuple[0]
            if field.startswith("PADDING_"):
                continue
            value = getattr(self, field)
            type_ = type(value)
            if hasattr(value, "_length_") and hasattr(value, "_type_"):
                # array
                if not hasattr(type_, "as_dict"):
                    value = [v for v in value]
                else:
                    type_ = type_._type_
                    value = [type_.as_dict(v) for v in value]
            elif hasattr(value, "contents") and hasattr(value, "_type_"):
                # pointer
                try:
                    if not hasattr(type_, "as_dict"):
                        value = value.contents
                    else:
                        type_ = type_._type_
                        value = type_.as_dict(value.contents)
                except ValueError:
                    # nullptr
                    value = None
            elif isinstance(value, AsDictMixin):
                # other structure
                value = type_.as_dict(value)
            result[field] = value
        return result


class _Structure(Structure, AsDictMixin):
    def __init__(self, *args, **kwds):
        # We don't want to use positional arguments fill PADDING_* fields

        args = dict(zip(self.__class__._field_names_(), args))
        args.update(kwds)
        super(_Structure, self).__init__(**args)

    @classmethod
    def _field_names_(cls):
        if hasattr(cls, "_fields_"):
            return (f[0] for f in cls._fields_ if not f[0].startswith("PADDING"))
        else:
            return ()

    @classmethod
    def get_type(cls, field):
        for f in cls._fields_:
            if f[0] == field:
                return f[1]
        return None

    @classmethod
    def bind(cls, bound_fields):
        fields = {}
        for name, type_ in cls._fields_:
            if hasattr(type_, "restype"):
                if name in bound_fields:
                    if bound_fields[name] is None:
                        fields[name] = type_()
                    else:
                        # use a closure to capture the callback from the loop scope
                        fields[name] = type_((lambda callback: lambda *args: callback(*args))(bound_fields[name]))
                    del bound_fields[name]
                else:
                    # default callback implementation (does nothing)
                    try:
                        default_ = type_(0).restype().value
                    except TypeError:
                        default_ = None
                    fields[name] = type_((lambda default_: lambda *args: default_)(default_))
            else:
                # not a callback function, use default initialization
                if name in bound_fields:
                    fields[name] = bound_fields[name]
                    del bound_fields[name]
                else:
                    fields[name] = type_()
        if len(bound_fields) != 0:
            raise ValueError(
                "Cannot bind the following unknown callback(s) {}.{}".format(cls.__name__, bound_fields.keys())
            )
        return cls(**fields)


class _Union(Union, AsDictMixin):
    pass


def string_cast(char_pointer, encoding="utf-8", errors="strict"):
    value = cast(char_pointer, c_char_p).value
    if value is not None and encoding is not None:
        value = value.decode(encoding, errors=errors)
    return value


def char_pointer_cast(string, encoding="utf-8"):
    if encoding is not None:
        try:
            string = string.encode(encoding)
        except AttributeError:
            # In Python3, bytes has no encode attribute
            pass
    string = c_char_p(string)
    return cast(string, POINTER(c_char))


class struct_ddog_Vec_U8(_Structure):
    pass


struct_ddog_Vec_U8._pack_ = 1  # source:False
struct_ddog_Vec_U8._fields_ = [
    ("ptr", POINTER(c_ubyte)),
    ("len", c_uint64),
    ("capacity", c_uint64),
]

ddog_Vec_U8 = struct_ddog_Vec_U8


# ------------------------------------- #


# values for enumeration 'ddog_Option_VecU8_Tag'
ddog_Option_VecU8_Tag__enumvalues = {
    0: "DDOG_OPTION_VEC_U8_SOME_VEC_U8",
    1: "DDOG_OPTION_VEC_U8_NONE_VEC_U8",
}
DDOG_OPTION_VEC_U8_SOME_VEC_U8 = 0
DDOG_OPTION_VEC_U8_NONE_VEC_U8 = 1
ddog_Option_VecU8_Tag = c_uint32  # enum


class struct_ddog_Option_VecU8(_Structure):
    pass


class union_ddog_Option_VecU8_0(_Union):
    pass


class struct_ddog_Option_VecU8_0_0(_Structure):
    _pack_ = 1  # source:False
    _fields_ = [
        ("some", struct_ddog_Vec_U8),
    ]


union_ddog_Option_VecU8_0._pack_ = 1  # source:False
union_ddog_Option_VecU8_0._fields_ = [
    ("ddog_Option_VecU8_0_0", struct_ddog_Option_VecU8_0_0),
]

struct_ddog_Option_VecU8._pack_ = 1  # source:False
struct_ddog_Option_VecU8._fields_ = [
    ("tag", ddog_Option_VecU8_Tag),
    ("PADDING_0", c_ubyte * 4),
    ("ddog_Option_VecU8_0", union_ddog_Option_VecU8_0),
]

ddog_Option_VecU8 = struct_ddog_Option_VecU8
ddog_MaybeError = struct_ddog_Option_VecU8

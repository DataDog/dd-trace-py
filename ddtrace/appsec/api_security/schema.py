import enum
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from typing import Any

MAX_DEPTH = 20
MAX_GIRTH = 256
MAX_TYPES_IN_ARRAY = 10


class Type_Base(enum.Enum):
    """
    primitive types
    format : python_type = name_for_export
    any Python type name can be added here and will be used automatically in the subsequent code
    """

    NoneType = 1  # "Null"
    bool = 2  # "Bool"
    int = 4  # "Number"
    str = 8  # "String"


class Record(dict):
    pass


class Array(list):
    def __init__(self, element_count):
        # type: (int) -> None
        self.element_count = element_count


class CacheBank:
    """
    Associate unique id (int) to objects to keep track of them
    and check equality in constant time.
    """

    def __init__(self):
        # type: () -> None
        self._counter = 0
        self._str_dict = {}  # type: dict[Any, int]
        self._id_dict = {}  # type: dict[int, Any]

    def get_id(self, s):
        # type: (Any) -> int
        """
        get the id associated to the object.
        create it if it doesn't exist.
        """
        res = self._str_dict.get(s, self._counter)
        if res == self._counter:
            self._str_dict[s] = self._counter
            self._id_dict[self._counter] = s
            self._counter += 1
        return res

    def get_val(self, val_id):
        # type: (int) -> Any
        """
        get the object associated to an id.
        The id must exist.
        """
        return self._id_dict[val_id]


def create_key(t, meta=None):
    if meta:
        meta = tuple(sorted(meta.items()))
    if isinstance(t, (list, set)):
        t = tuple(sorted(t))
    else:
        t = (t,)
    return t + meta if meta else t


def _build_type(obj, depth, cache):
    # type: (Any, int, CacheBank) -> tuple[int, Any]
    if depth >= MAX_DEPTH:
        return cache.get_id(Type_Base.NoneType.value), [Type_Base.NoneType.value]
    elif isinstance(obj, list):
        elements_types = set()  # type: set[int]
        res_array = Array(len(obj))
        meta = {"len": res_array.element_count}
        for elem in obj:
            e_id, e_type = _build_type(elem, depth + 1, cache)
            if e_id not in elements_types:
                if len(elements_types) >= MAX_TYPES_IN_ARRAY:
                    meta["truncated"] = True
                    break
                res_array.append(e_type)
                elements_types.add(e_id)
        return cache.get_id(create_key(elements_types, meta)), [res_array, meta]
    elif isinstance(obj, dict):
        res_record = Record()
        record_types = []
        i = 0
        meta = {}
        for i, (key, value) in enumerate(obj.items()):
            if i >= MAX_GIRTH:
                meta["truncated"] = True
                break
            e_id, e_type = _build_type(value, depth + 1, cache)
            res_record[key] = e_type
            record_types.append((key, e_id))
        res_type = [res_record, meta] if meta else [res_record]
        return cache.get_id(create_key(record_types, meta)), res_type
    else:
        typename = type(obj).__name__
        type_base = getattr(Type_Base, typename, Type_Base.NoneType)
        return cache.get_id(type_base.value), [type_base.value]


def build_schema(obj):
    return _build_type(obj, 0, CacheBank())[1]


def get_json_schema(obj):
    import json

    return json.dumps(build_schema(obj), separators=",:")

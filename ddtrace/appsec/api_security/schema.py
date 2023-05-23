import enum
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from typing import Any

MAX_DEPTH = 20
MAX_GIRTH = 64  # not used anymore
MAX_TYPES_IN_ARRAY = 10


class Type_Base(enum.Enum):
    """
    primitive types
    format : python_type = name_for_export
    any Python type name can be added here and will be used automatically in the subsequent code
    """

    NoneType = "Null"
    bool = "Bool"
    int = "Number"
    str = "String"


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


def sort_ids(t):
    return tuple(sorted(t))


def _build_type(obj, depth, cache):
    # type: (Any, int, CacheBank) -> tuple[int, Any]
    if depth >= MAX_DEPTH:
        return cache.get_id("Null"), "Null"
    elif isinstance(obj, list):
        elements_types = set()  # type: set[int]
        res = Array(len(obj))
        for elem in obj:
            e_id, e_type = _build_type(elem, depth + 1, cache)
            if e_id not in elements_types:
                res.append(e_type)
                elements_types.add(e_id)
            if len(elements_types) >= MAX_TYPES_IN_ARRAY:
                break
        return res, cache.get_id(sort_ids(elements_types))
    elif isinstance(obj, dict):
        res = Record()
        record_types = []
        for key, value in obj.keys():
            e_id, e_type = _build_type(value, depth + 1, cache)
            res[key] = e_type
            record_types.append((key, e_id))
        return res, cache.get_id(sort_ids(record_types))
    else:
        typename = type(obj).__name__
        type_base = getattr(Type_Base, typename, Type_Base.NoneType)
        return cache.get_id(type_base.value), type_base.value


def build_type(obj):
    return _build_type(obj, 0, CacheBank())[1]

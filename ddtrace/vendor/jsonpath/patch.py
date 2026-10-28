"""JSON Patch, as per RFC 6902."""

from __future__ import annotations

import copy
import json
from abc import ABC
from abc import abstractmethod
from io import IOBase
from typing import Any
from typing import Dict
from typing import Iterable
from typing import List
from typing import Mapping
from typing import MutableMapping
from typing import MutableSequence
from typing import TypeVar
from typing import Union

from ._data import load_data
from .exceptions import JSONPatchError
from .exceptions import JSONPatchTestFailure
from .exceptions import JSONPointerError
from .exceptions import JSONPointerIndexError
from .exceptions import JSONPointerKeyError
from .exceptions import JSONPointerTypeError
from .pointer import UNDEFINED
from .pointer import JSONPointer


class Op(ABC):
    """One of the JSON Patch operations."""

    name = "base"

    @abstractmethod
    def apply(
        self, data: Union[MutableSequence[object], MutableMapping[str, object]]
    ) -> Union[MutableSequence[object], MutableMapping[str, object]]:
        """Apply this patch operation to _data_."""

    @abstractmethod
    def asdict(self) -> Dict[str, object]:
        """Return a dictionary representation of this operation."""


class OpAdd(Op):
    """The JSON Patch _add_ operation."""

    __slots__ = ("path", "value")

    name = "add"

    def __init__(self, path: JSONPointer, value: object) -> None:
        self.path = path
        self.value = value

    def apply(
        self, data: Union[MutableSequence[object], MutableMapping[str, object]]
    ) -> Union[MutableSequence[object], MutableMapping[str, object]]:
        """Apply this patch operation to _data_."""
        parent, obj = self.path.resolve_parent(data)
        if parent is None:
            # Replace the root object.
            # The following op, if any, will raise a JSONPatchError if needed.
            return self.value  # type: ignore

        target = self.path.parts[-1]
        if isinstance(parent, MutableSequence):
            if obj is UNDEFINED:
                if target == "-":
                    parent.append(self.value)
                else:
                    index = self.path._index(target)  # type: ignore # noqa: SLF001
                    if index == len(parent):
                        parent.append(self.value)
                    else:
                        raise JSONPatchError("index out of range")
            else:
                parent.insert(int(target), self.value)
        elif isinstance(parent, MutableMapping):
            parent[str(target)] = self.value
        else:
            raise JSONPatchError(
                f"unexpected operation on {parent.__class__.__name__!r}"
            )
        return data

    def asdict(self) -> Dict[str, object]:
        """Return a dictionary representation of this operation."""
        return {"op": self.name, "path": str(self.path), "value": self.value}


class OpAddNe(OpAdd):
    """A non-standard _add if not exists_ operation.

    This is like _OpAdd_, but only adds object/dict keys/values if they key does
    not already exist.

    **New in version 1.2.0**
    """

    __slots__ = ("path", "value")

    name = "addne"

    def apply(
        self, data: Union[MutableSequence[object], MutableMapping[str, object]]
    ) -> Union[MutableSequence[object], MutableMapping[str, object]]:
        """Apply this patch operation to _data_."""
        parent, obj = self.path.resolve_parent(data)
        if parent is None:
            # Replace the root object.
            # The following op, if any, will raise a JSONPatchError if needed.
            return self.value  # type: ignore

        target = self.path.parts[-1]
        if isinstance(parent, MutableSequence):
            if obj is UNDEFINED:
                parent.append(self.value)
            else:
                parent.insert(int(target), self.value)
        elif isinstance(parent, MutableMapping) and target not in parent:
            parent[target] = self.value
        return data


class OpAddAp(OpAdd):
    """A non-standard add operation that appends to arrays/lists .

    This is like _OpAdd_, but assumes an index of "-" if the path can not
    be resolved rather than raising a JSONPatchError.

    **New in version 1.2.0**
    """

    __slots__ = ("path", "value")

    name = "addap"

    def apply(
        self, data: Union[MutableSequence[object], MutableMapping[str, object]]
    ) -> Union[MutableSequence[object], MutableMapping[str, object]]:
        """Apply this patch operation to _data_."""
        parent, obj = self.path.resolve_parent(data)
        if parent is None:
            # Replace the root object.
            # The following op, if any, will raise a JSONPatchError if needed.
            return self.value  # type: ignore

        target = self.path.parts[-1]
        if isinstance(parent, MutableSequence):
            if obj is UNDEFINED:
                parent.append(self.value)
            else:
                parent.insert(int(target), self.value)
        elif isinstance(parent, MutableMapping):
            parent[target] = self.value
        else:
            raise JSONPatchError(
                f"unexpected operation on {parent.__class__.__name__!r}"
            )
        return data


class OpRemove(Op):
    """The JSON Patch _remove_ operation."""

    __slots__ = ("path",)

    name = "remove"

    def __init__(self, path: JSONPointer) -> None:
        self.path = path

    def apply(
        self, data: Union[MutableSequence[object], MutableMapping[str, object]]
    ) -> Union[MutableSequence[object], MutableMapping[str, object]]:
        """Apply this patch operation to _data_."""
        parent, obj = self.path.resolve_parent(data)
        if parent is None:
            raise JSONPatchError("can't remove root")

        if isinstance(parent, MutableSequence):
            if obj is UNDEFINED:
                raise JSONPatchError("can't remove nonexistent item")
            del parent[int(self.path.parts[-1])]
        elif isinstance(parent, MutableMapping):
            if obj is UNDEFINED:
                raise JSONPatchError("can't remove nonexistent property")
            del parent[str(self.path.parts[-1])]
        else:
            raise JSONPatchError(
                f"unexpected operation on {parent.__class__.__name__!r}"
            )
        return data

    def asdict(self) -> Dict[str, object]:
        """Return a dictionary representation of this operation."""
        return {"op": self.name, "path": str(self.path)}


class OpReplace(Op):
    """The JSON Patch _replace_ operation."""

    __slots__ = ("path", "value")

    name = "replace"

    def __init__(self, path: JSONPointer, value: object) -> None:
        self.path = path
        self.value = value

    def apply(
        self, data: Union[MutableSequence[object], MutableMapping[str, object]]
    ) -> Union[MutableSequence[object], MutableMapping[str, object]]:
        """Apply this patch operation to _data_."""
        parent, obj = self.path.resolve_parent(data)
        if parent is None:
            return self.value  # type: ignore

        if isinstance(parent, MutableSequence):
            if obj is UNDEFINED:
                raise JSONPatchError("can't replace nonexistent item")
            parent[int(self.path.parts[-1])] = self.value
        elif isinstance(parent, MutableMapping):
            if obj is UNDEFINED:
                raise JSONPatchError("can't replace nonexistent property")
            parent[str(self.path.parts[-1])] = self.value
        else:
            raise JSONPatchError(
                f"unexpected operation on {parent.__class__.__name__!r}"
            )
        return data

    def asdict(self) -> Dict[str, object]:
        """Return a dictionary representation of this operation."""
        return {"op": self.name, "path": str(self.path), "value": self.value}


class OpMove(Op):
    """The JSON Patch _move_ operation."""

    __slots__ = ("source", "dest")

    name = "move"

    def __init__(self, from_: JSONPointer, path: JSONPointer) -> None:
        self.source = from_
        self.dest = path

    def apply(
        self, data: Union[MutableSequence[object], MutableMapping[str, object]]
    ) -> Union[MutableSequence[object], MutableMapping[str, object]]:
        """Apply this patch operation to _data_."""
        if self.dest.is_relative_to(self.source):
            raise JSONPatchError("can't move object to one of its own children")

        source_parent, source_obj = self.source.resolve_parent(data)

        if source_obj is UNDEFINED:
            raise JSONPatchError("source object does not exist")

        if isinstance(source_parent, MutableSequence):
            del source_parent[int(self.source.parts[-1])]
        if isinstance(source_parent, MutableMapping):
            del source_parent[str(self.source.parts[-1])]

        dest_parent, _ = self.dest.resolve_parent(data)

        if dest_parent is None:
            # Move source to root
            return source_obj  # type: ignore

        if isinstance(dest_parent, MutableSequence):
            dest_parent.insert(int(self.dest.parts[-1]), source_obj)
        elif isinstance(dest_parent, MutableMapping):
            dest_parent[str(self.dest.parts[-1])] = source_obj
        else:
            raise JSONPatchError(
                f"unexpected operation on {dest_parent.__class__.__name__!r}"
            )

        return data

    def asdict(self) -> Dict[str, object]:
        """Return a dictionary representation of this operation."""
        return {"op": self.name, "from": str(self.source), "path": str(self.dest)}


class OpCopy(Op):
    """The JSON Patch _copy_ operation."""

    __slots__ = ("source", "dest")

    name = "copy"

    def __init__(self, from_: JSONPointer, path: JSONPointer) -> None:
        self.source = from_
        self.dest = path

    def apply(
        self, data: Union[MutableSequence[object], MutableMapping[str, object]]
    ) -> Union[MutableSequence[object], MutableMapping[str, object]]:
        """Apply this patch operation to _data_."""
        _, source_obj = self.source.resolve_parent(data)

        if source_obj is UNDEFINED:
            raise JSONPatchError("source object does not exist")

        dest_parent, _ = self.dest.resolve_parent(data)

        if dest_parent is None:
            # Copy source to root
            return copy.deepcopy(source_obj)  # type: ignore

        if isinstance(dest_parent, MutableSequence):
            dest_parent.insert(int(self.dest.parts[-1]), copy.deepcopy(source_obj))
        elif isinstance(dest_parent, MutableMapping):
            dest_parent[str(self.dest.parts[-1])] = copy.deepcopy(source_obj)
        else:
            raise JSONPatchError(
                f"unexpected operation on {dest_parent.__class__.__name__!r}"
            )

        return data

    def asdict(self) -> Dict[str, object]:
        """Return a dictionary representation of this operation."""
        return {"op": self.name, "from": str(self.source), "path": str(self.dest)}


class OpTest(Op):
    """The JSON Patch _test_ operation."""

    __slots__ = ("path", "value")

    name = "test"

    def __init__(self, path: JSONPointer, value: object) -> None:
        self.path = path
        self.value = value

    def apply(
        self, data: Union[MutableSequence[object], MutableMapping[str, object]]
    ) -> Union[MutableSequence[object], MutableMapping[str, object]]:
        """Apply this patch operation to _data_."""
        _, obj = self.path.resolve_parent(data)
        if not obj == self.value:
            raise JSONPatchTestFailure
        return data

    def asdict(self) -> Dict[str, object]:
        """Return a dictionary representation of this operation."""
        return {"op": self.name, "path": str(self.path), "value": self.value}


Self = TypeVar("Self", bound="JSONPatch")


class JSONPatch:
    """Modify JSON-like data with JSON Patch.

    RFC 6902 defines operations to manipulate a JSON document. `JSONPatch`
    supports parsing and applying standard JSON Patch formatted operations,
    and provides a Python builder API following the same semantics as RFC 6902.

    Arguments:
        ops: A JSON Patch formatted document or equivalent Python objects.
        unicode_escape: If `True`, UTF-16 escape sequences will be decoded
            before parsing JSON pointers.
        uri_decode: If `True`, JSON pointers will be unescaped using _urllib_
            before being parsed.

    Raises:
        JSONPatchError: If _ops_ is given and any of the provided operations
            is malformed.
    """

    def __init__(
        self,
        ops: Union[str, IOBase, Iterable[Mapping[str, object]], None] = None,
        *,
        unicode_escape: bool = True,
        uri_decode: bool = False,
    ) -> None:
        self.ops: List[Op] = []
        self.unicode_escape = unicode_escape
        self.uri_decode = uri_decode
        if ops:
            self._load(ops)

    def _load(self, patch: Union[str, IOBase, Iterable[Mapping[str, object]]]) -> None:
        if isinstance(patch, IOBase):
            _patch = json.loads(patch.read())
        elif isinstance(patch, str):
            _patch = json.loads(patch)
        else:
            _patch = patch

        try:
            self._build(_patch)
        except TypeError as err:
            raise JSONPatchError(
                "expected a sequence of patch operations, "
                f"found {_patch.__class__.__name__!r}"
            ) from err

    def _build(self, patch: Iterable[Mapping[str, object]]) -> None:
        for i, operation in enumerate(patch):
            try:
                op = operation["op"]
            except KeyError as err:
                raise JSONPatchError(f"missing 'op' member at op {i}") from err

            if op == "add":
                self.add(
                    path=self._op_pointer(operation, "path", "add", i),
                    value=self._op_value(operation, "value", "add", i),
                )
            elif op == "addne":
                self.addne(
                    path=self._op_pointer(operation, "path", "addne", i),
                    value=self._op_value(operation, "value", "addne", i),
                )
            elif op == "addap":
                self.addap(
                    path=self._op_pointer(operation, "path", "addap", i),
                    value=self._op_value(operation, "value", "addap", i),
                )
            elif op == "remove":
                self.remove(path=self._op_pointer(operation, "path", "add", i))
            elif op == "replace":
                self.replace(
                    path=self._op_pointer(operation, "path", "replace", i),
                    value=self._op_value(operation, "value", "replace", i),
                )
            elif op == "move":
                self.move(
                    from_=self._op_pointer(operation, "from", "move", i),
                    path=self._op_pointer(operation, "path", "move", i),
                )
            elif op == "copy":
                self.copy(
                    from_=self._op_pointer(operation, "from", "copy", i),
                    path=self._op_pointer(operation, "path", "copy", i),
                )
            elif op == "test":
                self.test(
                    path=self._op_pointer(operation, "path", "test", i),
                    value=self._op_value(operation, "value", "test", i),
                )
            else:
                raise JSONPatchError(
                    "expected 'op' to be one of 'add', 'remove', 'replace', "
                    f"'move', 'copy' or 'test' ({op}:{i})"
                )

    def _op_pointer(
        self, operation: Mapping[str, object], key: str, op: str, i: int
    ) -> JSONPointer:
        try:
            pointer = operation[key]
        except KeyError as err:
            raise JSONPatchError(f"missing property {key!r} ({op}:{i})") from err

        if not isinstance(pointer, str):
            raise JSONPatchError(
                f"expected a JSON Pointer string for {key!r}, "
                f"found {pointer.__class__.__name__!r} "
                f"({op}:{i})"
            )

        try:
            return JSONPointer(
                pointer, unicode_escape=self.unicode_escape, uri_decode=self.uri_decode
            )
        except JSONPointerError as err:
            raise JSONPatchError(f"{err} ({op}:{i})") from err

    def _op_value(
        self, operation: Mapping[str, object], key: str, op: str, i: int
    ) -> object:
        try:
            return operation[key]
        except KeyError as err:
            raise JSONPatchError(f"missing property {key!r} ({op}:{i})") from err

    def _ensure_pointer(self, path: Union[str, JSONPointer]) -> JSONPointer:
        if isinstance(path, str):
            return JSONPointer(
                path,
                unicode_escape=self.unicode_escape,
                uri_decode=self.uri_decode,
            )
        assert isinstance(path, JSONPointer)
        return path

    def add(self: Self, path: Union[str, JSONPointer], value: object) -> Self:
        """Append an _add_ operation to this patch.

        Arguments:
            path: A string representation of a JSON Pointer, or one that has
                already been parsed.
            value: The object to add.

        Returns:
            This `JSONPatch` instance, so we can build a JSON Patch by chaining
                calls to JSON Patch operation methods.
        """
        pointer = self._ensure_pointer(path)
        self.ops.append(OpAdd(path=pointer, value=value))
        return self

    def addne(self: Self, path: Union[str, JSONPointer], value: object) -> Self:
        """Append an _addne_ operation to this patch.

        Arguments:
            path: A string representation of a JSON Pointer, or one that has
                already been parsed.
            value: The object to add.

        Returns:
            This `JSONPatch` instance, so we can build a JSON Patch by chaining
                calls to JSON Patch operation methods.
        """
        pointer = self._ensure_pointer(path)
        self.ops.append(OpAddNe(path=pointer, value=value))
        return self

    def addap(self: Self, path: Union[str, JSONPointer], value: object) -> Self:
        """Append an _addap_ operation to this patch.

        Arguments:
            path: A string representation of a JSON Pointer, or one that has
                already been parsed.
            value: The object to add.

        Returns:
            This `JSONPatch` instance, so we can build a JSON Patch by chaining
                calls to JSON Patch operation methods.
        """
        pointer = self._ensure_pointer(path)
        self.ops.append(OpAddAp(path=pointer, value=value))
        return self

    def remove(self: Self, path: Union[str, JSONPointer]) -> Self:
        """Append a _remove_ operation to this patch.

        Arguments:
            path: A string representation of a JSON Pointer, or one that has
                already been parsed.

        Returns:
            This `JSONPatch` instance, so we can build a JSON Patch by chaining
                calls to JSON Patch operation methods.
        """
        pointer = self._ensure_pointer(path)
        self.ops.append(OpRemove(path=pointer))
        return self

    def replace(self: Self, path: Union[str, JSONPointer], value: object) -> Self:
        """Append a _replace_ operation to this patch.

        Arguments:
            path: A string representation of a JSON Pointer, or one that has
                already been parsed.
            value: The object to add.

        Returns:
            This `JSONPatch` instance, so we can build a JSON Patch by chaining
                calls to JSON Patch operation methods.
        """
        pointer = self._ensure_pointer(path)
        self.ops.append(OpReplace(path=pointer, value=value))
        return self

    def move(
        self: Self, from_: Union[str, JSONPointer], path: Union[str, JSONPointer]
    ) -> Self:
        """Append a _move_ operation to this patch.

        Arguments:
            from_: A string representation of a JSON Pointer, or one that has
                already been parsed.
            path: A string representation of a JSON Pointer, or one that has
                already been parsed.

        Returns:
            This `JSONPatch` instance, so we can build a JSON Patch by chaining
                calls to JSON Patch operation methods.
        """
        source_pointer = self._ensure_pointer(from_)
        dest_pointer = self._ensure_pointer(path)
        self.ops.append(OpMove(from_=source_pointer, path=dest_pointer))
        return self

    def copy(
        self: Self, from_: Union[str, JSONPointer], path: Union[str, JSONPointer]
    ) -> Self:
        """Append a _copy_ operation to this patch.

        Arguments:
            from_: A string representation of a JSON Pointer, or one that has
                already been parsed.
            path: A string representation of a JSON Pointer, or one that has
                already been parsed.

        Returns:
            This `JSONPatch` instance, so we can build a JSON Patch by chaining
                calls to JSON Patch operation methods.
        """
        source_pointer = self._ensure_pointer(from_)
        dest_pointer = self._ensure_pointer(path)
        self.ops.append(OpCopy(from_=source_pointer, path=dest_pointer))
        return self

    def test(self: Self, path: Union[str, JSONPointer], value: object) -> Self:
        """Append a test operation to this patch.

        Arguments:
            path: A string representation of a JSON Pointer, or one that has
                already been parsed.
            value: The object to test.

        Returns:
            This `JSONPatch` instance, so we can build a JSON Patch by chaining
                calls to JSON Patch operation methods.
        """
        pointer = self._ensure_pointer(path)
        self.ops.append(OpTest(path=pointer, value=value))
        return self

    def apply(
        self,
        data: Union[str, IOBase, MutableSequence[Any], MutableMapping[str, Any]],
    ) -> object:
        """Apply all operations from this patch to _data_.

        If _data_ is a string or file-like object, it will be loaded with
        _json.loads_. Otherwise _data_ should be a JSON-like data structure and
        will be modified in place, even if a patch operation fails.

        When modifying _data_ in place, we return modified data too. This is
        to allow for replacing _data's_ root element, which is allowed by some
        patch operations.

        Arguments:
            data: The target JSON "document" or equivalent Python objects.

        Returns:
            Modified input data.

        Raises:
            JSONPatchError: When a patch operation fails.
            JSONPatchTestFailure: When a _test_ operation does not pass.
                `JSONPatchTestFailure` is a subclass of `JSONPatchError`.
        """
        _data = load_data(data)

        for i, op in enumerate(self.ops):
            try:
                _data = op.apply(_data)
            except JSONPatchTestFailure as err:
                raise JSONPatchTestFailure(f"test failed ({op.name}:{i})") from err
            except JSONPointerKeyError as err:
                raise JSONPatchError(f"{err} ({op.name}:{i})") from err
            except JSONPointerIndexError as err:
                raise JSONPatchError(f"{err} ({op.name}:{i})") from err
            except JSONPointerTypeError as err:
                raise JSONPatchError(f"{err} ({op.name}:{i})") from err
            except (JSONPointerError, JSONPatchError) as err:
                raise JSONPatchError(f"{err} ({op.name}:{i})") from err

        return _data

    def atomic(
        self,
        data: Union[List[Any], Dict[str, Any]],
    ) -> object:
        """Apply this patch to _data_ atomically.

        Unlike `apply()`, if any patch operation fails, _data_ remains
        unchanged.

        Arguments:
            data: A Python object representing JSON-like data.

        Returns:
            Patched _data_.

        Raises:
            JSONPatchError: When a patch operation fails.
            JSONPatchTestFailure: When a _test_ operation does not pass.
                `JSONPatchTestFailure` is a subclass of `JSONPatchError`.
        """
        data_ = self.apply(copy.deepcopy(data))  # This could raise a JSONPatchError.

        if isinstance(data, dict) and isinstance(data_, dict):
            data.clear()
            data.update(data_)  # type: ignore
            return data

        if isinstance(data, list) and isinstance(data_, list):
            data.clear()
            data.extend(data_)  # type: ignore
            return data

        return data_

    def patched(
        self,
        data: Union[List[Any], Dict[str, Any]],
    ) -> object:
        """Apply this patch to a deep copy of _data_.

        Arguments:
            data: JSON-like data.

        Returns:
            A patched copy of _data_.

        Raises:
            JSONPatchError: When a patch operation fails.
            JSONPatchTestFailure: When a _test_ operation does not pass.
                `JSONPatchTestFailure` is a subclass of `JSONPatchError`.
        """
        return self.apply(copy.deepcopy(data))

    def asdicts(self) -> List[Dict[str, object]]:
        """Return a list of this patch's operations as dictionaries."""
        return [op.asdict() for op in self.ops]


def apply(
    patch: Union[str, IOBase, Iterable[Mapping[str, object]], None],
    data: Union[str, IOBase, MutableSequence[Any], MutableMapping[str, Any]],
    *,
    unicode_escape: bool = True,
    uri_decode: bool = False,
) -> object:
    """Apply the JSON Patch _patch_ to _data_.

    If _data_ is a string or file-like object, it will be loaded with
    _json.loads_. Otherwise _data_ should be a JSON-like data structure and
    will be **modified in-place**, even if a patch operation fails.

    When modifying _data_ in-place, we return modified data too. This is
    to allow for replacing _data's_ root element, which is allowed by some
    patch operations.

    Arguments:
        patch: A JSON Patch formatted document or equivalent Python objects.
        data: The target JSON "document" or equivalent Python objects.
        unicode_escape: If `True`, UTF-16 escape sequences will be decoded
            before parsing JSON pointers.
        uri_decode: If `True`, JSON pointers will be unescaped using _urllib_
            before being parsed.

    Returns:
        Modified input data.

    Raises:
        JSONPatchError: When a patch operation fails.
        JSONPatchTestFailure: When a _test_ operation does not pass.
            `JSONPatchTestFailure` is a subclass of `JSONPatchError`.
    """
    return JSONPatch(
        patch,
        unicode_escape=unicode_escape,
        uri_decode=uri_decode,
    ).apply(data)


def atomic(
    patch: Union[str, Iterable[Mapping[str, object]], None],
    data: Union[List[Any], Dict[str, Any]],
    *,
    unicode_escape: bool = True,
    uri_decode: bool = False,
) -> object:
    """Apply patch operations from _patch_ to _data_ atomically.

    Unlike `apply()`, if any patch operation fails, _data_ remains unchanged.

    Arguments:
        patch: A JSON Patch formatted document or equivalent Python objects.
        data: A Python object representing JSON-like data.
        unicode_escape: If `True`, UTF-16 escape sequences will be decoded
            before parsing JSON pointers.
        uri_decode: If `True`, JSON pointers will be unescaped using _urllib_
            before being parsed.

    Returns:
        Patched _data_.

    Raises:
        JSONPatchError: When a patch operation fails.
        JSONPatchTestFailure: When a _test_ operation does not pass.
            `JSONPatchTestFailure` is a subclass of `JSONPatchError`.
    """
    return JSONPatch(
        patch,
        unicode_escape=unicode_escape,
        uri_decode=uri_decode,
    ).atomic(data)


def patched(
    patch: Union[str, Iterable[Mapping[str, object]], None],
    data: Union[List[Any], Dict[str, Any]],
    *,
    unicode_escape: bool = True,
    uri_decode: bool = False,
) -> object:
    """Apply patch operations from _patch_ to a deep copy of _data_.

    Arguments:
        patch: A JSON Patch formatted document or equivalent Python objects.
        data: JSON-like data.
        unicode_escape: If `True`, UTF-16 escape sequences will be decoded
            before parsing JSON pointers.
        uri_decode: If `True`, JSON pointers will be unescaped using _urllib_
            before being parsed.

    Returns:
        A patched copy of _data_.

    Raises:
        JSONPatchError: When a patch operation fails.
        JSONPatchTestFailure: When a _test_ operation does not pass.
            `JSONPatchTestFailure` is a subclass of `JSONPatchError`.
    """
    return JSONPatch(
        patch,
        unicode_escape=unicode_escape,
        uri_decode=uri_decode,
    ).patched(data)

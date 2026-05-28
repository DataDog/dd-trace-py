r"""Debugger expression language

This module implements the debugger expression language that is used in the UI
to define probe conditions and metric expressions. The JSON AST is compiled into
Python bytecode.

Full grammar:

    predicate               =>  <direct_predicate> | <arg_predicate> | <value_source>
    direct_predicate        =>  {"<direct_predicate_type>": <predicate>}
    direct_predicate_type   =>  not | isEmpty | isDefined
    value_source            =>  <literal> | <operation>
    literal                 =>  <number> | true | false | "string"
    number                  =>  0 | ([1-9][0-9]*\.[0-9]+)
    identifier              =>  <str.isidentifier>
    arg_predicate           =>  {"<arg_predicate_type>": [<argument_list>]}
    arg_predicate_type      =>  eq | ne | gt | ge | lt | le | any | all | and | or
                                | startsWith | endsWith | contains | matches
    argument_list           =>  <predicate>(,<predicate>)+
    operation               =>  <direct_operation> | <arg_operation>
    direct_opearation       =>  {"<direct_op_type>": <value_source>}
    direct_op_type          =>  len | count | ref
    arg_operation           =>  {"<arg_op_type>": [<argument_list>]}
    arg_op_type             =>  filter | substring | getmember | index
"""  # noqa

from dataclasses import dataclass
from decimal import Decimal
from itertools import chain
import re
import sys
from types import FunctionType
from typing import Any
from typing import Callable
from typing import Collection
from typing import Mapping
from typing import Optional
from typing import Union
from typing import cast

from bytecode import BinaryOp
from bytecode import Bytecode
from bytecode import Compare
from bytecode import Instr
from bytecode import Label

from ddtrace.debugging._safety import safe_getitem
from ddtrace.internal.compat import PYTHON_VERSION_INFO as PY
from ddtrace.internal.logger import get_logger
from ddtrace.internal.safety import _isinstance


DDASTType = Union[dict[str, Any], dict[str, list[Any]], Any]

log = get_logger(__name__)

# Types whose constructor is safe to call with a generator argument during
# filter reconstruction. Custom subclasses are excluded to avoid triggering
# arbitrary __init__ side effects.
_SAFE_RECONSTRUCTIBLE_TYPES: frozenset[type] = frozenset({list, tuple, set, frozenset})

# Direct handles on type's own C-level getset_descriptors.
# _type_dict_descriptor: calling .__get__(cls) returns the class namespace
# (tp_dict) as a mappingproxy, bypassing any metaclass __dict__ override.
# _type_module_descriptor / _type_qualname_descriptor: calling .__get__(cls)
# reads tp_module / tp_name directly, bypassing any metaclass __module__ or
# __qualname__ property that could invoke arbitrary user code.
_type_dict_descriptor: Any = type.__dict__["__dict__"]  # type: ignore[index]
_type_module_descriptor: Any = type.__dict__["__module__"]  # type: ignore[index]
_type_qualname_descriptor: Any = type.__dict__["__qualname__"]  # type: ignore[index]

# Builtin sized types whose __len__ is safe to call directly.  We call the
# unbound method (e.g. list.__len__(obj)) rather than the builtin len() so
# that custom __len__ overrides on subclasses are bypassed and cannot trigger
# arbitrary side effects (DB queries, network calls, …).
_SAFE_SIZED_BUILTINS: tuple[type, ...] = (list, tuple, set, frozenset, dict, str, bytes, bytearray, range)


def _safe_len(obj: Any) -> int:
    """Return the length of *obj* without invoking a custom __len__.

    Only succeeds for instances of known builtin sized types; raises TypeError
    for anything else to avoid triggering arbitrary user code as a side effect.
    """
    for t in _SAFE_SIZED_BUILTINS:
        if _isinstance(obj, t):
            return t.__len__(obj)  # type: ignore[attr-defined, no-any-return]
    raise TypeError("Cannot safely determine length of %s without risking side effects" % type(obj).__name__)


def _safe_is_empty(obj: Any) -> bool:
    """Return True if *obj* is empty, without invoking custom __bool__ or __len__."""
    return _safe_len(obj) == 0


# Builtin container types whose __contains__ is safe to call directly.
_SAFE_CONTAINS_BUILTINS: tuple[type, ...] = (list, tuple, set, frozenset, dict, str, bytes, bytearray)


def _safe_contains(container: Any, item: Any) -> bool:
    """Check membership without invoking a custom __contains__.

    Only succeeds for instances of known builtin container types.  Transparent
    proxies (e.g. SafeObjectProxy / wrapt.ObjectProxy) are unwrapped one level
    via object.__getattribute__ so that the underlying builtin container can be
    inspected safely.  Raises TypeError for anything else.
    """
    for t in _SAFE_CONTAINS_BUILTINS:
        if _isinstance(container, t):
            return t.__contains__(container, item)  # type: ignore[operator, no-any-return]
    # Unwrap transparent proxies (wrapt.ObjectProxy / SafeObjectProxy expose
    # __wrapped__ as a C-level attribute; object.__getattribute__ bypasses any
    # Python-level __getattribute__ override, reaching it directly).
    try:
        wrapped = object.__getattribute__(container, "__wrapped__")
    except AttributeError:
        pass
    else:
        return _safe_contains(wrapped, item)
    raise TypeError("Cannot safely check containment in %s without risking side effects" % type(container).__name__)


def _is_one_shot_iterator(it: Any) -> bool:
    """Return True if *it* is a one-shot iterator (sync or async).

    Uses MRO inspection rather than hasattr() to avoid triggering
    __getattribute__ overrides on the instance itself. Accesses __mro__
    via object.__getattribute__ to bypass any metaclass __getattribute__
    override, and reads each class's namespace through the raw type
    __dict__ descriptor to bypass any metaclass __dict__ override.
    """
    mro = object.__getattribute__(type(it), "__mro__")
    return any(
        "__next__" in _type_dict_descriptor.__get__(c) or "__anext__" in _type_dict_descriptor.__get__(c) for c in mro
    )


def _is_identifier(name: str) -> bool:
    return isinstance(name, str) and name.isidentifier()


def short_circuit_instrs(op: str, label: Label) -> list[Instr]:
    value = "FALSE" if op == "and" else "TRUE"
    if PY >= (3, 13):
        return [Instr("COPY", 1), Instr("TO_BOOL"), Instr(f"POP_JUMP_IF_{value}", label), Instr("POP_TOP")]
    elif PY >= (3, 12):
        return [Instr("COPY", 1), Instr(f"POP_JUMP_IF_{value}", label), Instr("POP_TOP")]

    return [Instr(f"JUMP_IF_{value}_OR_POP", label)]


def instanceof(value: Any, type_qname: str) -> bool:
    try:
        # Try with a built-in type first
        return isinstance(value, __builtins__[type_qname])  # type: ignore[index]
    except KeyError:
        # Otherwise we expect a fully qualified name
        try:
            for c in object.__getattribute__(type(value), "__mro__"):
                # Use the raw type-level getset_descriptors for __module__ and
                # __qualname__ so that a custom metaclass property for either
                # attribute cannot be invoked as a side effect.
                module = _type_module_descriptor.__get__(c)
                qualname = _type_qualname_descriptor.__get__(c)
                if f"{module}.{qualname}" == type_qname:
                    return True
        except Exception:
            log.debug("Failed to check instanceof %s for value of type %s", type_qname, type(value))

    return False


def isdefined(predicate: Callable[[Mapping[str, Any]], Any], _locals: Mapping[str, Any]) -> bool:
    try:
        predicate(_locals)
    except BaseException:
        return False
    return True


def get_local(_locals: Mapping[str, Any], name: str) -> Any:
    try:
        return _locals[name]
    except KeyError:
        raise NameError(f"No such local variable: '{name}'")


class DDCompiler:
    def __init__(self) -> None:
        self._lambda_level = 0

    @classmethod
    def __getmember__(cls, o: Any, a: str) -> Any:
        return object.__getattribute__(o, a)

    @classmethod
    def __index__(cls, o: Any, i: Any) -> Any:
        return safe_getitem(o, i)

    @classmethod
    def __ref__(cls, x: Any) -> Any:
        return x

    def _make_function(self, instrs: list[Instr], args: tuple[str, ...], name: str) -> FunctionType:
        abstract_code = Bytecode([*instrs, Instr("RETURN_VALUE")])

        abstract_code.argcount = len(args)
        abstract_code.argnames = list(args)
        abstract_code.name = name

        if sys.version_info >= (3, 11):
            abstract_code.insert(0, Instr("RESUME", 0))

        return FunctionType(abstract_code.to_code(), {}, name, (), None)

    def _make_lambda(self, ast: DDASTType) -> Callable[[Any, Any], Any]:
        self._lambda_level += 1
        if (predicate := self._compile_predicate(ast)) is None:
            raise ValueError("Invalid predicate: %r" % ast)

        try:
            return self._make_function(predicate, ("_dd_it", "_dd_key", "_dd_value", "_locals"), "<lambda>")
        finally:
            assert self._lambda_level > 0  # nosec
            self._lambda_level -= 1

    def _compile_direct_predicate(self, ast: DDASTType) -> Optional[list[Instr]]:
        # direct_predicate       =>  {"<direct_predicate_type>": <predicate>}
        # direct_predicate_type  =>  not | isEmpty | isDefined
        if not isinstance(ast, dict):
            return None

        _type, arg = next(iter(ast.items()))

        if _type not in {"not", "isEmpty", "isDefined"}:
            return None

        value = self._compile_predicate(arg)
        if value is None:
            raise ValueError("Invalid argument: %r" % arg)

        if _type == "isDefined":
            value = self._call_function(
                isdefined,
                [Instr("LOAD_CONST", self._make_function(value, ("_locals",), "<isDefined-predicate>"))],
                [Instr("LOAD_FAST", "_locals")],
            )
        elif _type == "isEmpty":
            value = self._call_function(_safe_is_empty, value)
        else:  # "not"
            if PY >= (3, 13):
                # UNARY_NOT requires a boolean value
                value.append(Instr("TO_BOOL"))
            value.append(Instr("UNARY_NOT"))

        return value

    def _compile_arg_predicate(self, ast: DDASTType) -> Optional[list[Instr]]:
        # arg_predicate       =>  {"<arg_predicate_type>": [<argument_list>]}
        # arg_predicate_type  =>  eq | ne | gt | ge | lt | le | any | all | and | or
        #                            | startsWith | endsWith | contains | matches
        if not isinstance(ast, dict):
            return None

        _type, args = next(iter(ast.items()))

        if _type in {"or", "and"}:
            a, b = args
            ca, cb = self._compile_predicate(a), self._compile_predicate(b)
            if ca is None:
                raise ValueError("Invalid argument: %r" % a)
            if cb is None:
                raise ValueError("Invalid argument: %r" % b)

            short_circuit = Label()
            return ca + short_circuit_instrs(_type, short_circuit) + cb + cast(list[Instr], [short_circuit])

        if _type in {"eq", "ge", "gt", "le", "lt", "ne"}:
            a, b = args
            ca, cb = self._compile_predicate(a), self._compile_predicate(b)
            if ca is None:
                raise ValueError("Invalid argument: %r" % a)
            if cb is None:
                raise ValueError("Invalid argument: %r" % b)
            return ca + cb + [Instr("COMPARE_OP", getattr(Compare, _type.upper()))]

        if _type == "contains":
            a, b = args
            ca, cb = self._compile_predicate(a), self._compile_predicate(b)
            if ca is None:
                raise ValueError("Invalid argument: %r" % a)
            if cb is None:
                raise ValueError("Invalid argument: %r" % b)
            return self._call_function(_safe_contains, ca, cb)

        if _type in {"any", "all"}:
            a, b = args
            f = __builtins__[_type]  # type: ignore[index]
            ca, fb = self._compile_predicate(a), self._make_lambda(b)

            if ca is None:
                raise ValueError("Invalid argument: %r" % a)

            def coll_iter(
                it: Collection[Any], cond: Callable[[Any, Any, Any, dict[str, Any]], bool], _locals: dict[str, Any]
            ) -> Any:
                if _is_one_shot_iterator(it):
                    raise TypeError("Cannot iterate over a one-shot iterator in a debugger expression")
                if _isinstance(it, dict):
                    # Use the unbound dict.items() to bypass any subclass override.
                    return f(cond(k, k, v, _locals) for k, v in dict.items(it))  # type: ignore[arg-type, var-annotated]
                return f(cond(e, None, None, _locals) for e in it)

            return self._call_function(coll_iter, ca, [Instr("LOAD_CONST", fb)], [Instr("LOAD_FAST", "_locals")])

        if _type in {"startsWith", "endsWith"}:
            a, b = args
            ca, cb = self._compile_predicate(a), self._compile_predicate(b)
            if ca is None:
                raise ValueError("Invalid argument: %r" % a)
            if cb is None:
                raise ValueError("Invalid argument: %r" % b)
            return self._call_function(getattr(str, _type.lower()), ca, cb)

        if _type == "matches":
            a, b = args
            string, pattern = self._compile_predicate(a), self._compile_predicate(b)
            if string is None:
                raise ValueError("Invalid argument: %r" % a)
            if pattern is None:
                raise ValueError("Invalid argument: %r" % b)
            return self._call_function(lambda p, s: re.match(p, s) is not None, pattern, string)

        return None

    def _compile_direct_operation(self, ast: DDASTType) -> Optional[list[Instr]]:
        # direct_opearation  =>  {"<direct_op_type>": <value_source>}
        # direct_op_type     =>  len | count | ref
        if not isinstance(ast, dict):
            return None

        _type, arg = next(iter(ast.items()))

        if _type in {"len", "count"}:
            value = self._compile_value_source(arg)
            if value is None:
                raise ValueError("Invalid argument: %r" % arg)
            return self._call_function(_safe_len, value)

        if _type == "ref":
            if not isinstance(arg, str):
                return None

            if arg in {"@it", "@key", "@value"}:
                if self._lambda_level <= 0:
                    msg = f"Invalid use of {arg} outside of lambda"
                    raise ValueError(msg)
                return [Instr("LOAD_FAST", f"_dd_{arg[1:]}")]

            return self._call_function(
                get_local, [Instr("LOAD_FAST", "_locals")], [Instr("LOAD_CONST", self.__ref__(arg))]
            )

        return None

    def _call_function(self, func: Callable[..., Any], *args: list[Instr]) -> list[Instr]:
        _func: Any = func  # Instr does not accept a Callable
        if PY >= (3, 13):
            return [Instr("LOAD_CONST", _func), Instr("PUSH_NULL")] + list(chain(*args)) + [Instr("CALL", len(args))]
        if PY >= (3, 12):
            return [Instr("PUSH_NULL"), Instr("LOAD_CONST", _func)] + list(chain(*args)) + [Instr("CALL", len(args))]
        if PY >= (3, 11):
            return (
                [Instr("PUSH_NULL"), Instr("LOAD_CONST", _func)]
                + list(chain(*args))
                + [Instr("PRECALL", len(args)), Instr("CALL", len(args))]
            )

        return [Instr("LOAD_CONST", _func)] + list(chain(*args)) + [Instr("CALL_FUNCTION", len(args))]

    def _compile_arg_operation(self, ast: DDASTType) -> Optional[list[Instr]]:
        # arg_operation  =>  {"<arg_op_type>": [<argument_list>]}
        # arg_op_type    =>  filter | substring | getmember | index | instanceof
        if not isinstance(ast, dict):
            return None

        _type, args = next(iter(ast.items()))

        if _type not in {"filter", "substring", "getmember", "index", "instanceof"}:
            return None

        if _type == "substring":
            v, a, b = args
            cv, ca, cb = self._compile_predicate(v), self._compile_predicate(a), self._compile_predicate(b)
            if cv is None:
                raise ValueError("Invalid argument: %r" % v)
            if ca is None:
                raise ValueError("Invalid argument: %r" % a)
            if cb is None:
                raise ValueError("Invalid argument: %r" % b)

            if PY >= (3, 14):
                subscr_instruction = Instr("BINARY_OP", BinaryOp.SUBSCR)
            else:
                subscr_instruction = Instr("BINARY_SUBSCR")
            return cv + ca + cb + [Instr("BUILD_SLICE", 2), subscr_instruction]

        if _type == "filter":
            a, b = args
            ca, fb = self._compile_predicate(a), self._make_lambda(b)

            if ca is None:
                raise ValueError("Invalid argument: %r" % a)

            def coll_filter(
                it: Any, cond: Callable[[Any, Any, Any, dict[str, Any]], bool], _locals: dict[str, Any]
            ) -> Any:
                if _is_one_shot_iterator(it):
                    raise TypeError("Cannot iterate over a one-shot iterator in a debugger expression")
                if _isinstance(it, dict):
                    # Use unbound dict.items() to bypass any subclass override, and
                    # return a plain dict to avoid calling a custom subclass constructor.
                    return {k: v for k, v in dict.items(it) if cond(k, k, v, _locals)}
                it_type = type(it)
                filtered = (e for e in it if cond(e, None, None, _locals))
                # Only reconstruct using the original type for known-safe builtins.
                # For custom subclasses, fall back to list to avoid __init__ side effects.
                return it_type(filtered) if it_type in _SAFE_RECONSTRUCTIBLE_TYPES else list(filtered)

            return self._call_function(coll_filter, ca, [Instr("LOAD_CONST", fb)], [Instr("LOAD_FAST", "_locals")])

        if _type == "getmember":
            v, attr = args
            if not _is_identifier(attr):
                raise ValueError("Invalid identifier: %r" % attr)

            cv = self._compile_predicate(v)
            if not cv:
                return None

            return self._call_function(self.__getmember__, cv, [Instr("LOAD_CONST", attr)])

        if _type == "index":
            v, i = args
            cv = self._compile_predicate(v)
            if not cv:
                return None
            ci = self._compile_predicate(i)
            if not ci:
                return None
            return self._call_function(self.__index__, cv, ci)

        if _type == "instanceof":
            v, t = args
            cv = self._compile_predicate(v)
            if not cv:
                return None
            ct = self._compile_predicate(t)
            if not ct:
                return None
            return self._call_function(instanceof, cv, ct)

        return None

    def _compile_operation(self, ast: DDASTType) -> Optional[list[Instr]]:
        # operation  =>  <direct_operation> | <arg_operation>
        return self._compile_direct_operation(ast) or self._compile_arg_operation(ast)

    def _compile_literal(self, ast: DDASTType) -> Optional[list[Instr]]:
        # literal  =>  <number> | true | false | "string" | null
        if not (isinstance(ast, (str, int, float, bool, Decimal)) or ast is None):
            return None

        return [Instr("LOAD_CONST", ast)]

    def _compile_value_source(self, ast: DDASTType) -> Optional[list[Instr]]:
        # value_source  =>  <literal> | <operation>
        return self._compile_operation(ast) or self._compile_literal(ast)

    def _compile_predicate(self, ast: DDASTType) -> Optional[list[Instr]]:
        # predicate  =>  <direct_predicate> | <arg_predicate> | <value_source>
        return (
            self._compile_direct_predicate(ast) or self._compile_arg_predicate(ast) or self._compile_value_source(ast)
        )

    def compile(self, ast: DDASTType) -> Callable[[Mapping[str, Any]], Any]:
        if (predicate := self._compile_predicate(ast)) is None:
            raise ValueError("Invalid predicate: %r" % ast)

        return self._make_function(predicate, ("_locals",), "<expr>")


dd_compile = DDCompiler().compile


class DDExpressionEvaluationError(Exception):
    """Thrown when an error occurs while evaluating a dsl expression."""

    def __init__(self, dsl: str, e: Exception) -> None:
        super().__init__('Failed to evaluate expression "%s": %s' % (dsl, str(e)))
        self.dsl = dsl
        self.error = str(e)


def _invalid_expression(_: Any) -> None:
    """Forces probes with invalid expression/conditions to never trigger.

    Any signs of invalid conditions in logs is an indication of a problem with
    the expression compiler.
    """
    return None


@dataclass
class DDExpression:
    __compiler__ = dd_compile

    dsl: str
    callable: Callable[[Mapping[str, Any]], Any]

    def eval(self, scope: Mapping[str, Any]) -> Any:
        try:
            return self.callable(scope)
        except Exception as e:
            raise DDExpressionEvaluationError(self.dsl, e) from e

    def __call__(self, scope: Mapping[str, Any]) -> Any:
        return self.eval(scope)

    @classmethod
    def on_compiler_error(cls, dsl: str, exc: Exception) -> Callable[[Mapping[str, Any]], Any]:
        log.error("Cannot compile expression: %s", dsl, exc_info=True)
        return _invalid_expression

    @classmethod
    def compile(cls, expr: Mapping[str, Any]) -> "DDExpression":
        ast = expr["json"]
        dsl = expr["dsl"]

        try:
            compiled = cls.__compiler__(ast)
        except Exception as e:
            compiled = cls.on_compiler_error(dsl, e)

        return cls(dsl=dsl, callable=compiled)

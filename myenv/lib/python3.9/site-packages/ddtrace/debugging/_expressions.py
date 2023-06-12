r"""Debugger expression language

This module implements the debugger expression language that is used in the UI
to define probe conditions and metric expressions. The JSON AST is compiled into
Python bytecode.

Full grammar:

    predicate               =>  <direct_predicate> | <arg_predicate> | <value_source>
    direct_predicate        =>  {"<direct_predicate_type>": <predicate>}
    direct_predicate_type   =>  not | isEmpty | isUndefined
    value_source            =>  <literal> | <operation>
    literal                 =>  <number> | true | false | "string"
    number                  =>  0 | ([1-9][0-9]*\.[0-9]+)
    identifier              =>  [a-zA-Z][a-zA-Z0-9_]*
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
from itertools import chain
import re
from types import FunctionType
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union

import attr
from bytecode import Bytecode
from bytecode import Compare
from bytecode import Instr

from ddtrace.debugging.safety import safe_getitem
from ddtrace.internal.compat import PYTHON_VERSION_INFO as PY


DDASTType = Union[Dict[str, Any], Dict[str, List[Any]], Any]

IDENT_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]*")


def _make_function(ast, args, name):
    # type: (DDASTType, Tuple[str,...], str) -> FunctionType
    compiled = _compile_predicate(ast)
    if compiled is None:
        raise ValueError("Invalid predicate: %r" % ast)

    instrs = compiled + [Instr("RETURN_VALUE")]

    abstract_code = Bytecode(instrs)
    abstract_code.argcount = len(args)
    abstract_code.argnames = args
    abstract_code.name = name

    return FunctionType(abstract_code.to_code(), {}, name, (), None)


def _make_lambda(ast):
    # type: (DDASTType) -> Callable[[Any, Any], Any]
    return _make_function(ast, ("_dd_it", "_locals"), "<lambda>")


def _compile_direct_predicate(ast):
    # type: (DDASTType) -> Optional[List[Instr]]
    # direct_predicate       =>  {"<direct_predicate_type>": <predicate>}
    # direct_predicate_type  =>  not | isEmpty | isUndefined
    if not isinstance(ast, dict):
        return None

    _type, arg = next(iter(ast.items()))

    if _type not in {"not", "isEmpty"}:
        return None

    value = _compile_predicate(arg)
    if value is None:
        raise ValueError("Invalid argument: %r" % arg)

    value.append(Instr("UNARY_NOT"))

    # TODO: isUndefined will be implemented later

    return value


def _compile_arg_predicate(ast):
    # type: (DDASTType) -> Optional[List[Instr]]
    # arg_predicate       =>  {"<arg_predicate_type>": [<argument_list>]}
    # arg_predicate_type  =>  eq | ne | gt | ge | lt | le | any | all | and | or
    #                            | startsWith | endsWith | contains | matches
    if not isinstance(ast, dict):
        return None

    _type, args = next(iter(ast.items()))

    if _type in {"or", "and"}:
        a, b = args
        ca, cb = _compile_predicate(a), _compile_predicate(b)
        if ca is None:
            raise ValueError("Invalid argument: %r" % a)
        if cb is None:
            raise ValueError("Invalid argument: %r" % b)
        return ca + cb + [Instr("BINARY_%s" % _type.upper())]

    if _type in {"eq", "ge", "gt", "le", "lt", "ne"}:
        a, b = args
        ca, cb = _compile_predicate(a), _compile_predicate(b)
        if ca is None:
            raise ValueError("Invalid argument: %r" % a)
        if cb is None:
            raise ValueError("Invalid argument: %r" % b)
        return ca + cb + [Instr("COMPARE_OP", getattr(Compare, _type.upper()))]

    if _type == "contains":
        a, b = args
        ca, cb = _compile_predicate(a), _compile_predicate(b)
        if ca is None:
            raise ValueError("Invalid argument: %r" % a)
        if cb is None:
            raise ValueError("Invalid argument: %r" % b)
        return cb + ca + [Instr("COMPARE_OP", Compare.IN) if PY < (3, 9) else Instr("CONTAINS_OP", 0)]

    if _type in {"any", "all"}:
        a, b = args
        f = __builtins__[_type]  # type: ignore[index]
        ca, fb = _compile_predicate(a), _make_lambda(b)

        if ca is None:
            raise ValueError("Invalid argument: %r" % a)

        return _call_function(
            lambda i, c, _locals: f(c(_, _locals) for _ in i),
            ca,
            [Instr("LOAD_CONST", fb)],
            [Instr("LOAD_FAST", "_locals")],
        )

    if _type in {"startsWith", "endsWith"}:
        a, b = args
        ca, cb = _compile_predicate(a), _compile_predicate(b)
        if ca is None:
            raise ValueError("Invalid argument: %r" % a)
        if cb is None:
            raise ValueError("Invalid argument: %r" % b)
        return _call_function(getattr(str, _type.lower()), ca, cb)

    if _type == "matches":
        a, b = args
        string, pattern = _compile_predicate(a), _compile_predicate(b)
        if string is None:
            raise ValueError("Invalid argument: %r" % a)
        if pattern is None:
            raise ValueError("Invalid argument: %r" % b)
        return _call_function(lambda p, s: re.match(p, s) is not None, pattern, string)

    return None


def _compile_direct_operation(ast):
    # type: (DDASTType) -> Optional[List[Instr]]
    # direct_opearation  =>  {"<direct_op_type>": <value_source>}
    # direct_op_type     =>  len | count | ref
    if not isinstance(ast, dict):
        return None

    _type, arg = next(iter(ast.items()))

    if _type in {"len", "count"}:
        value = _compile_value_source(arg)
        if value is None:
            raise ValueError("Invalid argument: %r" % arg)
        return _call_function(len, value)

    if _type == "ref":
        if not isinstance(arg, str):
            return None

        if arg == "@it":
            return [Instr("LOAD_FAST", "_dd_it")]

        return [
            Instr("LOAD_FAST", "_locals"),
            Instr("LOAD_CONST", arg),
            Instr("BINARY_SUBSCR"),
        ]

    return None


def _call_function(func, *args):
    # type: (Callable, List[Instr]) -> List[Instr]
    if PY < (3, 11):
        return [Instr("LOAD_CONST", func)] + list(chain(*args)) + [Instr("CALL_FUNCTION", len(args))]
    return (
        [Instr("PUSH_NULL"), Instr("LOAD_CONST", func)]
        + list(chain(*args))
        + [Instr("PRECALL", len(args)), Instr("CALL", len(args))]
    )


def _compile_arg_operation(ast):
    # type: (DDASTType) -> Optional[List[Instr]]
    # arg_operation  =>  {"<arg_op_type>": [<argument_list>]}
    # arg_op_type    =>  filter | substring
    if not isinstance(ast, dict):
        return None

    _type, args = next(iter(ast.items()))

    if _type not in {"filter", "substring", "getmember", "index"}:
        return None

    if _type == "substring":
        v, a, b = args
        cv, ca, cb = _compile_predicate(v), _compile_predicate(a), _compile_predicate(b)
        if cv is None:
            raise ValueError("Invalid argument: %r" % v)
        if ca is None:
            raise ValueError("Invalid argument: %r" % a)
        if cb is None:
            raise ValueError("Invalid argument: %r" % b)
        return cv + ca + cb + [Instr("BUILD_SLICE", 2), Instr("BINARY_SUBSCR")]

    if _type == "filter":
        a, b = args
        ca, fb = _compile_predicate(a), _make_lambda(b)

        if ca is None:
            raise ValueError("Invalid argument: %r" % a)

        return _call_function(
            lambda i, c, _locals: type(i)(_ for _ in i if c(_, _locals)),
            ca,
            [Instr("LOAD_CONST", fb)],
            [Instr("LOAD_FAST", "_locals")],
        )

    if _type == "getmember":
        v, atr = args
        cv = _compile_predicate(v)
        if not cv:
            return None
        if not isinstance(atr, str) or not IDENT_RE.match(atr):
            return None

        return _call_function(object.__getattribute__, cv, [Instr("LOAD_CONST", atr)])

    if _type == "index":
        v, i = args
        cv = _compile_predicate(v)
        if not cv:
            return None
        ci = _compile_predicate(i)
        if not ci:
            return None
        return _call_function(safe_getitem, cv, ci)

    return None


def _compile_operation(ast):
    # type: (DDASTType) -> Optional[List[Instr]]
    # operation  =>  <direct_operation> | <arg_operation>
    return _compile_direct_operation(ast) or _compile_arg_operation(ast)


def _compile_literal(ast):
    # type: (DDASTType) -> Optional[List[Instr]]
    # literal  =>  <number> | true | false | "string"
    if not isinstance(ast, (str, int, float, bool)):
        return None

    return [Instr("LOAD_CONST", ast)]


def _compile_value_source(ast):
    # type: (DDASTType) -> Optional[List[Instr]]
    # value_source  =>  <literal> | <operation>
    return _compile_operation(ast) or _compile_literal(ast)


def _compile_predicate(ast):
    # type: (DDASTType) -> Optional[List[Instr]]
    # predicate  =>  <direct_predicate> | <arg_predicate> | <value_source>
    return _compile_direct_predicate(ast) or _compile_arg_predicate(ast) or _compile_value_source(ast)


def dd_compile(ast):
    # type: (DDASTType) -> Callable[[Dict[str, Any]], Any]
    return _make_function(ast, ("_locals",), "<expr>")


class DDExpressionEvaluationError(Exception):
    """Thrown when an error occurs while evaluating a dsl expression."""

    def __init__(self, dsl, e):
        super(DDExpressionEvaluationError, self).__init__('Failed to evaluate expression "%s": %s' % (dsl, str(e)))
        self.dsl = dsl
        self.error = str(e)


@attr.s
class DDExpression(object):
    dsl = attr.ib(type=str)  # type: str
    callable = attr.ib(type=Callable[[Dict[str, Any]], Any])  # type: Callable[[Dict[str, Any]], Any]

    def eval(self, _locals):
        try:
            return self.callable(_locals)
        except Exception as e:
            raise DDExpressionEvaluationError(self.dsl, e)

    def __call__(self, _locals):
        return self.eval(_locals)

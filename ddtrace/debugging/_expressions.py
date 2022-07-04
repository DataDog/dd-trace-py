r"""Debugger expression language

This module implements the debugger expression language that is used in the UI
to define probe conditions and metric expressions. The JSON AST is compiled into
Python bytecode.

Full grammar:

    predicate               =>  <direct_predicate> | <arg_predicate> | <value_source>
    direct_predicate        =>  {"<direct_predicate_type>": <predicate>}
    direct_predicate_type   =>  not | isEmpty | isUndefined
    value_source            =>  <literal> | <value_reference> | <operation>
    literal                 =>  <number> | true | false | "string"
    number                  =>  0 | ([1-9][0-9]*\.[0-9]+)
    value_reference         =>  "<reference_prefix><reference_path>"
    reference_prefix        =>  @ | ^ | # | .
    reference_path          =>  identifier(.identifier)*
    identifier              =>  [a-zA-Z][a-zA-Z0-9_]*
    arg_predicate           =>  {"<arg_predicate_type>": [<argument_list>]}
    arg_predicate_type      =>  eq | ne | gt | ge | lt | le | any | all | and | or
                                | startsWith | endsWith | contains | matches
    argument_list           =>  <predicate>(,<predicate>)+
    operation               =>  <direct_operation> | <arg_operation>
    direct_opearation       =>  {"<direct_op_type>": <value_source>}
    direct_op_type          =>  len | count
    arg_operation           =>  {"<arg_op_type>": [<argument_list>]}
    arg_op_type             =>  filter | substring
"""  # noqa
import re
from types import FunctionType
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union

from bytecode import Bytecode
from bytecode import Compare
from bytecode import Instr

from ddtrace.internal.compat import PYTHON_VERSION_INFO as PY


DDASTType = Union[Dict[str, Any], Dict[str, List[Any]], Any]

IDENT_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]*")


def _make_function(ast, args, name):
    # type: (DDASTType, Tuple[str], str) -> FunctionType
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
    # type: (DDASTType) -> Callable[[Any], Any]
    return _make_function(ast, ("_dd_it",), "<lambda>")


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
        return (
            [Instr("LOAD_CONST", lambda i, c: f(c(_) for _ in i))]
            + ca
            + [
                Instr("LOAD_CONST", fb),
                Instr("CALL_FUNCTION", 2),
            ]
        )

    if _type in {"startsWith", "endsWith"}:
        a, b = args
        ca, cb = _compile_predicate(a), _compile_predicate(b)
        if ca is None:
            raise ValueError("Invalid argument: %r" % a)
        if cb is None:
            raise ValueError("Invalid argument: %r" % b)
        return [Instr("LOAD_CONST", getattr(str, _type.lower()))] + ca + cb + [Instr("CALL_FUNCTION", 2)]

    if _type == "matches":
        a, b = args
        ca, cb = _compile_predicate(a), _compile_predicate(b)
        if ca is None:
            raise ValueError("Invalid argument: %r" % a)
        if cb is None:
            raise ValueError("Invalid argument: %r" % b)
        return [Instr("LOAD_CONST", lambda p, s: re.match(p, s) is not None)] + cb + ca + [Instr("CALL_FUNCTION", 2)]

    return None


def _compile_direct_operation(ast):
    # type: (DDASTType) -> Optional[List[Instr]]
    # direct_opearation  =>  {"<direct_op_type>": <value_source>}
    # direct_op_type     =>  len | count
    if not isinstance(ast, dict):
        return None

    _type, arg = next(iter(ast.items()))

    if _type in {"len", "count"}:
        value = _compile_value_source(arg)
        if value is None:
            raise ValueError("Invalid argument: %r" % arg)
        return [Instr("LOAD_CONST", len)] + value + [Instr("CALL_FUNCTION", 1)]

    return None


def _compile_arg_operation(ast):
    # type: (DDASTType) -> Optional[List[Instr]]
    # arg_operation  =>  {"<arg_op_type>": [<argument_list>]}
    # arg_op_type    =>  filter | substring
    if not isinstance(ast, dict):
        return None

    _type, args = next(iter(ast.items()))

    if _type not in {"filter", "substring"}:
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
        return (
            [Instr("LOAD_CONST", lambda i, c: type(i)(_ for _ in i if c(_)))]
            + ca
            + [
                Instr("LOAD_CONST", fb),
                Instr("CALL_FUNCTION", 2),
            ]
        )

    return None


def _compile_operation(ast):
    # type: (DDASTType) -> Optional[List[Instr]]
    # operation  =>  <direct_operation> | <arg_operation>
    return _compile_direct_operation(ast) or _compile_arg_operation(ast)


def _compile_identifier(ast):
    # type: (DDASTType) -> Optional[List[Instr]]
    # identifier  =>  [a-zA-Z][a-zA-Z0-9_]*
    if not isinstance(ast, str) or not IDENT_RE.match(ast):
        return None

    return [Instr("LOAD_ATTR", ast)]


def _compile_reference_path(ast):
    # type: (DDASTType) -> Optional[List[Instr]]
    # reference_path  =>  identifier(.identifier)*
    if not isinstance(ast, str):
        return None

    if not ast:
        return []

    head, _, tail = ast.partition(".")

    this = _compile_identifier(head)
    if this is None:
        raise ValueError("Invalid identifier: %s" % head)
    rest = _compile_reference_path(tail)
    if rest is None:
        raise ValueError("Invalid reference: %s" % ast)
    return this + rest


def _compile_value_reference(ast):
    # type: (DDASTType) -> Optional[List[Instr]]
    # value_reference   =>  "<reference_prefix><reference_path>"
    # reference_prefix  =>  @ | ^ | # | .
    if not isinstance(ast, str) or not ast:
        return None

    ref_ident = ast[0]

    if ref_ident not in {"#", "^", "@", "."}:
        return None

    head, _, tail = ast[1:].partition(".")
    if not IDENT_RE.match(head):
        raise ValueError("Invalid identifier: %s" % head)

    if ref_ident in {"#", "^"}:  # Locals and arguments (including self)
        path = _compile_reference_path(tail)
        if path is None:
            raise ValueError("Invalid reference: %s" % ast)
        return [
            Instr("LOAD_FAST", "_locals"),
            Instr("LOAD_CONST", head),
            Instr("BINARY_SUBSCR"),
        ] + path

    if ref_ident == ".":  # Attributes
        path = _compile_reference_path(ast[1:])
        if path is None:
            raise ValueError("Invalid reference: %s" % ast)
        return [
            Instr("LOAD_FAST", "_locals"),
            Instr("LOAD_CONST", "self"),
            Instr("BINARY_SUBSCR"),
        ] + path

    if ref_ident == "@" and head == "it":
        path = _compile_reference_path(tail)
        if path is None:
            raise ValueError("Invalid reference: %s" % ast)
        return [Instr("LOAD_FAST", "_dd_it")] + path

    return None


def _compile_literal(ast):
    # type: (DDASTType) -> Optional[List[Instr]]
    # literal  =>  <number> | true | false | "string"
    if not isinstance(ast, (str, int, float, bool)):
        return None

    return [Instr("LOAD_CONST", ast)]


def _compile_value_source(ast):
    # type: (DDASTType) -> Optional[List[Instr]]
    # value_source  =>  <literal> | <value_reference> | <operation>
    return _compile_operation(ast) or _compile_value_reference(ast) or _compile_literal(ast)


def _compile_predicate(ast):
    # type: (DDASTType) -> Optional[List[Instr]]
    # predicate  =>  <direct_predicate> | <arg_predicate> | <value_source>
    return _compile_direct_predicate(ast) or _compile_arg_predicate(ast) or _compile_value_source(ast)


def dd_compile(ast):
    # type: (DDASTType) -> Callable[[Dict[str, Any]], Any]
    return _make_function(ast, ("_locals",), "<expr>")

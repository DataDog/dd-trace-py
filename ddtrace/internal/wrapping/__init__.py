from types import FunctionType
from typing import Any
from typing import Callable
from typing import Dict
from typing import Optional
from typing import Tuple
from typing import cast

from six import PY3


try:
    from typing import Protocol
except ImportError:
    from typing_extensions import Protocol  # type: ignore[assignment]

import bytecode as bc
from bytecode import Bytecode
from bytecode import Instr

from ddtrace.internal.compat import PYTHON_VERSION_INFO as PY
from ddtrace.internal.wrapping.asyncs import wrap_async
from ddtrace.internal.wrapping.generators import wrap_generator


class WrappedFunction(Protocol):
    """A wrapped function."""

    __dd_wrapped__ = None  # type: Optional[FunctionType]
    __dd_wrappers__ = None  # type: Optional[Dict[Any, Any]]

    def __call__(self, *args, **kwargs):
        pass


Wrapper = Callable[[FunctionType, Tuple[Any], Dict[str, Any]], Any]


def _add(lineno):
    if PY >= (3, 11):
        return Instr("BINARY_OP", bc.BinaryOp.ADD, lineno=lineno)

    return Instr("INPLACE_ADD", lineno=lineno)


def _update_map_block(varkwargsname, lineno):
    if PY >= (3, 12):
        return [
            Instr("COPY", 1, lineno=lineno),
            Instr("LOAD_ATTR", (True, "update"), lineno=lineno),
            Instr("LOAD_FAST", varkwargsname, lineno=lineno),
            Instr("CALL", 1, lineno=lineno),
            Instr("POP_TOP", lineno=lineno),
        ]

    if PY >= (3, 11):
        return [
            Instr("COPY", 1, lineno=lineno),
            Instr("LOAD_METHOD", "update", lineno=lineno),
            Instr("LOAD_FAST", varkwargsname, lineno=lineno),
            Instr("PRECALL", 1, lineno=lineno),
            Instr("CALL", 1, lineno=lineno),
            Instr("POP_TOP", lineno=lineno),
        ]

    return [
        Instr("DUP_TOP", lineno=lineno),
        Instr("LOAD_ATTR", "update", lineno=lineno),
        Instr("LOAD_FAST", varkwargsname, lineno=lineno),
        Instr("CALL_FUNCTION", 1, lineno=lineno),
        Instr("POP_TOP", lineno=lineno),
    ]


def _call_return_block(arg, lineno):
    if PY >= (3, 12):
        return [
            Instr("CALL", arg, lineno=lineno),
            Instr("RETURN_VALUE", lineno=lineno),
        ]

    if PY >= (3, 11):
        return [
            Instr("PRECALL", arg, lineno=lineno),
            Instr("CALL", arg, lineno=lineno),
            Instr("RETURN_VALUE", lineno=lineno),
        ]

    return [
        Instr("CALL_FUNCTION", arg, lineno=lineno),
        Instr("RETURN_VALUE", lineno=lineno),
    ]


FIRSTLINENO_OFFSET = int(PY >= (3, 11))


def wrap_bytecode(wrapper, wrapped):
    # type: (Wrapper, FunctionType) -> Bytecode
    """Wrap a function with a wrapper function.

    The wrapper function expects the wrapped function as the first argument,
    followed by the tuple of arguments and the dictionary of keyword arguments.
    The nature of the wrapped function is also honored, meaning that a generator
    function will return a generator function, and a coroutine function will
    return a coroutine function, and so on. The signature is also preserved to
    avoid breaking, e.g., usages of the ``inspect`` module.
    """

    code = wrapped.__code__
    lineno = code.co_firstlineno + FIRSTLINENO_OFFSET
    varargs = bool(code.co_flags & bc.CompilerFlags.VARARGS)
    varkwargs = bool(code.co_flags & bc.CompilerFlags.VARKEYWORDS)
    nargs = code.co_argcount
    argnames = code.co_varnames[:nargs]
    try:
        kwonlyargs = code.co_kwonlyargcount
    except AttributeError:
        kwonlyargs = 0
    kwonlyargnames = code.co_varnames[nargs : nargs + kwonlyargs]
    varargsname = code.co_varnames[nargs + kwonlyargs] if varargs else None
    varkwargsname = code.co_varnames[nargs + kwonlyargs + varargs] if varkwargs else None

    # Push the wrapper function that is to be called and the wrapped function to
    # be passed as first argument.
    instrs = [
        Instr("LOAD_CONST", wrapper, lineno=lineno),
        Instr("LOAD_CONST", wrapped, lineno=lineno),
    ]
    if PY >= (3, 11):
        # From insert_prefix_instructions
        instrs[0:0] = [
            Instr("RESUME", 0, lineno=lineno - 1),
            Instr("PUSH_NULL", lineno=lineno),
        ]

        if code.co_cellvars:
            instrs[0:0] = [Instr("MAKE_CELL", bc.CellVar(_), lineno=lineno) for _ in code.co_cellvars]

        if code.co_freevars:
            instrs.insert(0, Instr("COPY_FREE_VARS", len(code.co_freevars), lineno=lineno))

    # Build the tuple of all the positional arguments
    if nargs:
        instrs.extend(
            [
                Instr("LOAD_DEREF", bc.CellVar(argname), lineno=lineno)
                if PY >= (3, 11) and argname in code.co_cellvars
                else Instr("LOAD_FAST", argname, lineno=lineno)
                for argname in argnames
            ]
        )
        instrs.append(Instr("BUILD_TUPLE", nargs, lineno=lineno))
        if varargs:
            instrs.extend(
                [
                    Instr("LOAD_FAST", varargsname, lineno=lineno),
                    _add(lineno),
                ]
            )
    elif varargs:
        instrs.append(Instr("LOAD_FAST", varargsname, lineno=lineno))
    else:
        instrs.append(Instr("BUILD_TUPLE", 0, lineno=lineno))

    # Prepare the keyword arguments
    if kwonlyargs:
        for arg in kwonlyargnames:
            instrs.extend(
                [
                    Instr("LOAD_CONST", arg, lineno=lineno),
                    Instr("LOAD_FAST", arg, lineno=lineno),
                ]
            )
        instrs.append(Instr("BUILD_MAP", kwonlyargs, lineno=lineno))
        if varkwargs:
            instrs.extend(_update_map_block(varkwargsname, lineno))

    elif varkwargs:
        instrs.append(Instr("LOAD_FAST", varkwargsname, lineno=lineno))

    else:
        instrs.append(Instr("BUILD_MAP", 0, lineno=lineno))

    # Call the wrapper function with the wrapped function, the positional and
    # keyword arguments, and return the result.
    instrs.extend(_call_return_block(3, lineno))

    # If the function has special flags set, like the generator, async generator
    # or coroutine, inject unraveling code before the return opcode.
    if bc.CompilerFlags.GENERATOR & code.co_flags and not (bc.CompilerFlags.COROUTINE & code.co_flags):
        wrap_generator(instrs, code, lineno)
    elif PY3:
        wrap_async(instrs, code, lineno)

    return Bytecode(instrs)


def wrap(f, wrapper):
    # type: (FunctionType, Wrapper) -> WrappedFunction
    """Wrap a function with a wrapper.

    The wrapper expects the function as first argument, followed by the tuple
    of positional arguments and the dict of keyword arguments.

    Note that this changes the behavior of the original function with the
    wrapper function, instead of creating a new function object.
    """
    wrapped = FunctionType(
        f.__code__,
        f.__globals__,
        "<wrapped>",
        f.__defaults__,
        f.__closure__,
    )
    try:
        wf = cast(WrappedFunction, f)
        cast(WrappedFunction, wrapped).__dd_wrapped__ = cast(FunctionType, wf.__dd_wrapped__)
    except AttributeError:
        pass

    if PY3:
        wrapped.__kwdefaults__ = f.__kwdefaults__

    code = wrap_bytecode(wrapper, wrapped)
    code.freevars = f.__code__.co_freevars
    if PY >= (3, 11):
        code.cellvars = f.__code__.co_cellvars
    code.name = f.__code__.co_name
    code.filename = f.__code__.co_filename
    code.flags = f.__code__.co_flags
    code.argcount = f.__code__.co_argcount
    try:
        code.posonlyargcount = f.__code__.co_posonlyargcount
    except AttributeError:
        pass

    nargs = code.argcount
    try:
        code.kwonlyargcount = f.__code__.co_kwonlyargcount
        nargs += code.kwonlyargcount
    except AttributeError:
        pass
    nargs += bool(code.flags & bc.CompilerFlags.VARARGS) + bool(code.flags & bc.CompilerFlags.VARKEYWORDS)
    code.argnames = f.__code__.co_varnames[:nargs]

    f.__code__ = code.to_code()

    # DEV: Multiple wrapping is implemented as a singly-linked list via the
    # __dd_wrapped__ attribute.
    wf = cast(WrappedFunction, f)
    wf.__dd_wrapped__ = wrapped

    return wf


def unwrap(wf, wrapper):
    # type: (WrappedFunction, Wrapper) -> FunctionType
    """Unwrap a wrapped function.

    This is the reverse of :func:`wrap`. In case of multiple wrapping layers,
    this will unwrap the one that uses ``wrapper``. If the function was not
    wrapped with ``wrapper``, it will return the first argument.
    """
    # DEV: Multiple wrapping layers are singly-linked via __dd_wrapped__. When
    # we find the layer that needs to be removed we also have to ensure that we
    # update the link at the deletion site if there is a non-empty tail.
    try:
        inner = cast(FunctionType, wf.__dd_wrapped__)

        # Sanity check
        assert inner.__name__ == "<wrapped>", "Wrapper has wrapped function"  # nosec

        if wrapper not in cast(FunctionType, wf).__code__.co_consts:
            # This is not the correct wrapping layer. Try with the next one.
            inner_wf = cast(WrappedFunction, inner)
            return unwrap(inner_wf, wrapper)

        # Remove the current wrapping layer by moving the next one over the
        # current one.
        f = cast(FunctionType, wf)
        f.__code__ = inner.__code__
        try:
            # Update the link to the next layer.
            inner_wf = cast(WrappedFunction, inner)
            wf.__dd_wrapped__ = inner_wf.__dd_wrapped__  # type: ignore[assignment]
        except AttributeError:
            # No more wrapping layers. Restore the original function by removing
            # this extra attribute.
            del wf.__dd_wrapped__

        return f

    except AttributeError:
        # The function is not wrapped so we return it as is.
        return cast(FunctionType, wf)

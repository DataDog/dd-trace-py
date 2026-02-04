import sys
from types import CodeType
from types import FunctionType
from typing import Any
from typing import Callable
from typing import Dict
from typing import Generator
from typing import Optional
from typing import Protocol
from typing import Tuple
from typing import cast

import bytecode as bc
from bytecode import Instr

from ddtrace.internal.assembly import Assembly
from ddtrace.internal.utils.inspection import link_function_to_code
from ddtrace.internal.wrapping.asyncs import wrap_async
from ddtrace.internal.wrapping.generators import wrap_generator


PY = sys.version_info[:2]


class WrappedFunction(Protocol):
    """A wrapped function."""

    __dd_wrapped__: Optional[FunctionType] = None
    __dd_wrappers__: Optional[Dict[Any, Any]] = None

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        pass


Wrapper = Callable[[FunctionType, Tuple[Any], Dict[str, Any]], Any]


def _add(lineno: int) -> Instr:
    if PY >= (3, 11):
        return Instr("BINARY_OP", bc.BinaryOp.ADD, lineno=lineno)

    return Instr("INPLACE_ADD", lineno=lineno)


HEAD = Assembly()
if PY >= (3, 13):
    HEAD.parse(
        r"""
            resume              0
            load_const          {wrapper}
            push_null
            load_const          {wrapped}
        """
    )

elif PY >= (3, 11):
    HEAD.parse(
        r"""
            resume              0
            push_null
            load_const          {wrapper}
            load_const          {wrapped}
        """
    )

else:
    HEAD.parse(
        r"""
            load_const          {wrapper}
            load_const          {wrapped}
        """
    )


UPDATE_MAP = Assembly()
if PY >= (3, 12):
    UPDATE_MAP.parse(
        r"""
            copy                1
            load_method         $update
            load_fast           {varkwargsname}
            call                1
            pop_top
        """
    )

elif PY >= (3, 11):
    UPDATE_MAP.parse(
        r"""
            copy                1
            load_method         $update
            load_fast           {varkwargsname}
            precall             1
            call                1
            pop_top
        """
    )

else:
    UPDATE_MAP.parse(
        r"""
            dup_top
            load_attr           $update
            load_fast           {varkwargsname}
            call_function       1
            pop_top
        """
    )


CALL_RETURN = Assembly()
if PY >= (3, 12):
    CALL_RETURN.parse(
        r"""
            call                {arg}
            return_value
        """
    )

elif PY >= (3, 11):
    CALL_RETURN.parse(
        r"""
            precall             {arg}
            call                {arg}
            return_value
        """
    )

else:
    CALL_RETURN.parse(
        r"""
            call_function       {arg}
            return_value
        """
    )


FIRSTLINENO_OFFSET = int(PY >= (3, 11))


def generate_posargs(code: CodeType) -> Generator[Instr, None, None]:
    """Generate the opcodes for building the positional arguments tuple."""
    varnames = code.co_varnames
    lineno = code.co_firstlineno + FIRSTLINENO_OFFSET
    varargs = bool(code.co_flags & bc.CompilerFlags.VARARGS)
    nargs = code.co_argcount
    varargsname = varnames[nargs + code.co_kwonlyargcount] if varargs else None

    if nargs:  # posargs [+ varargs]
        yield from (
            Instr("LOAD_DEREF", bc.CellVar(argname), lineno=lineno)
            if PY >= (3, 11) and argname in code.co_cellvars
            else Instr("LOAD_FAST", argname, lineno=lineno)
            for argname in varnames[:nargs]
        )

        yield Instr("BUILD_TUPLE", nargs, lineno=lineno)
        if varargs:
            yield Instr("LOAD_FAST", varargsname, lineno=lineno)
            yield _add(lineno)

    elif varargs:  # varargs
        yield Instr("LOAD_FAST", varargsname, lineno=lineno)

    else:  # ()
        yield Instr("BUILD_TUPLE", 0, lineno=lineno)


(PAIR := Assembly()).parse(
    r"""
        load_const          {arg}
        load_fast           {arg}
    """
)


def generate_kwargs(code: CodeType) -> Generator[Instr, None, None]:
    """Generate the opcodes for building the keyword arguments dictionary."""
    flags = code.co_flags
    varnames = code.co_varnames
    lineno = code.co_firstlineno + FIRSTLINENO_OFFSET
    varargs = bool(flags & bc.CompilerFlags.VARARGS)
    varkwargs = bool(flags & bc.CompilerFlags.VARKEYWORDS)
    nargs = code.co_argcount
    kwonlyargs = code.co_kwonlyargcount
    varkwargsname = varnames[nargs + kwonlyargs + varargs] if varkwargs else None

    if kwonlyargs:
        for arg in varnames[nargs : nargs + kwonlyargs]:  # kwargs [+ varkwargs]
            yield from PAIR.bind({"arg": arg}, lineno=lineno)
        yield Instr("BUILD_MAP", kwonlyargs, lineno=lineno)
        if varkwargs:
            yield from UPDATE_MAP.bind({"varkwargsname": varkwargsname}, lineno=lineno)

    elif varkwargs:  # varkwargs
        yield Instr("LOAD_FAST", varkwargsname, lineno=lineno)

    else:  # {}
        yield Instr("BUILD_MAP", 0, lineno=lineno)


def wrap_bytecode(wrapper: Wrapper, wrapped: FunctionType) -> bc.Bytecode:
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

    # Push the wrapper function that is to be called and the wrapped function to
    # be passed as first argument.
    instrs = HEAD.bind({"wrapper": wrapper, "wrapped": wrapped}, lineno=lineno)

    # Add positional arguments
    instrs.extend(generate_posargs(code))

    # Add keyword arguments
    instrs.extend(generate_kwargs(code))

    # Call the wrapper function with the wrapped function, the positional and
    # keyword arguments, and return the result. This is equivalent to
    #
    #   >>> return wrapper(wrapped, args, kwargs)
    instrs.extend(CALL_RETURN.bind({"arg": 3}, lineno=lineno))

    # Include code for handling free/cell variables, if needed
    if PY >= (3, 11):
        if code.co_cellvars:
            instrs[0:0] = [Instr("MAKE_CELL", bc.CellVar(_), lineno=lineno) for _ in code.co_cellvars]

        if code.co_freevars:
            instrs.insert(0, Instr("COPY_FREE_VARS", len(code.co_freevars), lineno=lineno))

    # If the function has special flags set, like the generator, async generator
    # or coroutine, inject unraveling code before the return opcode.
    if (bc.CompilerFlags.GENERATOR & code.co_flags) and not (bc.CompilerFlags.COROUTINE & code.co_flags):
        wrap_generator(instrs, code, lineno)
    else:
        wrap_async(instrs, code, lineno)

    return instrs


def wrap(f: FunctionType, wrapper: Wrapper) -> WrappedFunction:
    """Wrap a function with a wrapper.

    The wrapper expects the function as first argument, followed by the tuple
    of positional arguments and the dict of keyword arguments.

    Note that this changes the behavior of the original function with the
    wrapper function, instead of creating a new function object.
    """
    wrapped = FunctionType(
        code := f.__code__,
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

    wrapped.__kwdefaults__ = f.__kwdefaults__

    flags = code.co_flags
    nargs = (
        (argcount := code.co_argcount)
        + (kwonlycount := code.co_kwonlyargcount)
        + bool(flags & bc.CompilerFlags.VARARGS)
        + bool(flags & bc.CompilerFlags.VARKEYWORDS)
    )

    # Wrap the wrapped function with the wrapper
    wrapped_code = wrap_bytecode(wrapper, wrapped)

    # Copy over the code attributes
    wrapped_code.argcount = argcount
    wrapped_code.argnames = code.co_varnames[:nargs]
    wrapped_code.filename = code.co_filename
    wrapped_code.freevars = code.co_freevars
    wrapped_code.flags = flags
    wrapped_code.kwonlyargcount = kwonlycount
    wrapped_code.name = code.co_name
    wrapped_code.posonlyargcount = code.co_posonlyargcount
    if PY >= (3, 11):
        wrapped_code.cellvars = code.co_cellvars

    # Replace the function code with the trampoline bytecode
    f.__code__ = wrapped_code.to_code()

    # DEV: Multiple wrapping is implemented as a singly-linked list via the
    # __dd_wrapped__ attribute.
    wf = cast(WrappedFunction, f)
    wf.__dd_wrapped__ = wrapped

    # Link the original code object to the original function
    link_function_to_code(code, f)

    return wf


def is_wrapped(f: FunctionType) -> bool:
    """Check if a function is wrapped with any wrapper."""
    try:
        wf = cast(WrappedFunction, f)
        inner = cast(FunctionType, wf.__dd_wrapped__)

        # Sanity check
        assert inner.__name__ == "<wrapped>", "Wrapper has wrapped function"  # nosec
        return True
    except AttributeError:
        return False


def is_wrapped_with(f: FunctionType, wrapper: Wrapper) -> bool:
    """Check if a function is wrapped with a specific wrapper."""
    try:
        wf = cast(WrappedFunction, f)
        inner = cast(FunctionType, wf.__dd_wrapped__)

        # Sanity check
        assert inner.__name__ == "<wrapped>", "Wrapper has wrapped function"  # nosec

        if wrapper in f.__code__.co_consts:
            return True

        # This is not the correct wrapping layer. Try with the next one.
        return is_wrapped_with(inner, wrapper)

    except AttributeError:
        return False


def unwrap(wf: WrappedFunction, wrapper: Wrapper) -> FunctionType:
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
    except AttributeError:
        # The function is not wrapped so we return it as is.
        return cast(FunctionType, wf)

    # Sanity check
    assert inner.__name__ == "<wrapped>", "Wrapper has wrapped function"  # nosec

    if wrapper not in cast(FunctionType, wf).__code__.co_consts:
        # This is not the correct wrapping layer. Try with the next one.
        return unwrap(cast(WrappedFunction, inner), wrapper)

    # Remove the current wrapping layer by moving the next one over the
    # current one.
    f = cast(FunctionType, wf)
    f.__code__ = inner.__code__

    try:
        # Update the link to the next layer.
        wf.__dd_wrapped__ = cast(WrappedFunction, inner).__dd_wrapped__  # type: ignore[assignment]
    except AttributeError:
        # No more wrapping layers. Restore the original function by removing
        # this extra attribute.
        del wf.__dd_wrapped__

    return f


def get_function_code(f: FunctionType) -> CodeType:
    return (cast(WrappedFunction, f).__dd_wrapped__ or f if is_wrapped(f) else f).__code__


def set_function_code(f: FunctionType, code: CodeType) -> None:
    (cast(WrappedFunction, f).__dd_wrapped__ or f if is_wrapped(f) else f).__code__ = code  # type: ignore[misc]

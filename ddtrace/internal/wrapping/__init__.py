import sys
from types import CodeType
from types import FunctionType
from typing import Any
from typing import Callable
from typing import Iterator
from typing import MutableMapping
from typing import Optional
from typing import Protocol
from typing import cast
import weakref

import bytecode as bc
from bytecode import Instr

from ddtrace.internal.assembly import Assembly
from ddtrace.internal.threads import Lock
from ddtrace.internal.wrapping.asyncs import wrap_async
from ddtrace.internal.wrapping.generators import wrap_generator


PY = sys.version_info[:2]

# Maps each wrapped function to its inner copy (the singly-linked list of
# wrapping layers). WeakKeyDictionary so functions are not kept alive by the
# registry alone.
_wrapped: weakref.WeakKeyDictionary[FunctionType, FunctionType] = weakref.WeakKeyDictionary()
_wrapped_lock = Lock()

# Maps original code objects to the functions that own them. Written by
# link_function_to_code; read by functions_for_code in inspection.py.
_code_to_fn: MutableMapping[CodeType, FunctionType] = {}


def link_function_to_code(code: CodeType, function: FunctionType) -> None:
    """Link a function to its original code object for fast reverse lookup."""
    _code_to_fn[code] = function


class WrappedFunction(Protocol):
    """A wrapped function."""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        pass


Wrapper = Callable[[FunctionType, tuple[Any], dict[str, Any]], Any]


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


_UPDATE_MAP_FAST = Assembly()
_UPDATE_MAP_DEREF = Assembly()
if PY >= (3, 12):
    _UPDATE_MAP_FAST.parse(
        r"""
            copy                1
            load_method         $update
            load_fast           {varkwargsname}
            call                1
            pop_top
        """
    )
    _UPDATE_MAP_DEREF.parse(
        r"""
            copy                1
            load_method         $update
            load_deref          {varkwargsname}
            call                1
            pop_top
        """
    )
elif PY >= (3, 11):
    _UPDATE_MAP_FAST.parse(
        r"""
            copy                1
            load_method         $update
            load_fast           {varkwargsname}
            precall             1
            call                1
            pop_top
        """
    )
    _UPDATE_MAP_DEREF.parse(
        r"""
            copy                1
            load_method         $update
            load_deref          {varkwargsname}
            precall             1
            call                1
            pop_top
        """
    )
else:
    _UPDATE_MAP_FAST.parse(
        r"""
            dup_top
            load_attr           $update
            load_fast           {varkwargsname}
            call_function       1
            pop_top
        """
    )
    _UPDATE_MAP_DEREF.parse(
        r"""
            dup_top
            load_attr           $update
            load_deref          {varkwargsname}
            call_function       1
            pop_top
        """
    )


def _generate_update_map(name: str, code: CodeType, lineno: int) -> Iterator[Any]:
    """Yield opcodes to call ``dict.update(name)`` where ``name`` may be a cell var."""
    if PY >= (3, 11) and name in code.co_cellvars:
        yield from _UPDATE_MAP_DEREF.bind({"varkwargsname": bc.CellVar(name)}, lineno=lineno)  # type: ignore[attr-defined]
    else:
        yield from _UPDATE_MAP_FAST.bind({"varkwargsname": name}, lineno=lineno)


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


def _load_var(name: str, code: CodeType, lineno: int) -> Instr:
    """Return the correct load instruction for a function parameter.

    On Python 3.11+, parameters captured by inner closures become cell
    variables and must be loaded with LOAD_DEREF instead of LOAD_FAST.
    """
    if PY >= (3, 11) and name in code.co_cellvars:
        return Instr("LOAD_DEREF", bc.CellVar(name), lineno=lineno)  # type: ignore[attr-defined]
    return Instr("LOAD_FAST", name, lineno=lineno)


def generate_posargs(code: CodeType) -> Iterator[Any]:
    """Generate the opcodes for building the positional arguments tuple."""
    varnames = code.co_varnames
    lineno = code.co_firstlineno + FIRSTLINENO_OFFSET
    varargs = bool(code.co_flags & bc.CompilerFlags.VARARGS)
    nargs = code.co_argcount
    varargsname: Optional[str] = varnames[nargs + code.co_kwonlyargcount] if varargs else None

    if nargs:  # posargs [+ varargs]
        yield from (_load_var(argname, code, lineno) for argname in varnames[:nargs])

        yield Instr("BUILD_TUPLE", nargs, lineno=lineno)
        if varargsname is not None:
            yield _load_var(varargsname, code, lineno)
            yield _add(lineno)

    elif varargsname is not None:  # varargs
        yield _load_var(varargsname, code, lineno)

    else:  # ()
        yield Instr("BUILD_TUPLE", 0, lineno=lineno)


def generate_kwargs(code: CodeType) -> Iterator[Any]:
    """Generate the opcodes for building the keyword arguments dictionary."""
    flags = code.co_flags
    varnames = code.co_varnames
    lineno = code.co_firstlineno + FIRSTLINENO_OFFSET
    varargs = bool(flags & bc.CompilerFlags.VARARGS)
    varkwargs = bool(flags & bc.CompilerFlags.VARKEYWORDS)
    nargs = code.co_argcount
    kwonlyargs = code.co_kwonlyargcount
    varkwargsname: Optional[str] = varnames[nargs + kwonlyargs + varargs] if varkwargs else None

    if kwonlyargs:
        for arg in varnames[nargs : nargs + kwonlyargs]:  # kwargs [+ varkwargs]
            yield Instr("LOAD_CONST", arg, lineno=lineno)
            yield _load_var(arg, code, lineno)
        yield Instr("BUILD_MAP", kwonlyargs, lineno=lineno)
        if varkwargsname is not None:
            yield from _generate_update_map(varkwargsname, code, lineno)

    elif varkwargsname is not None:  # varkwargs
        yield _load_var(varkwargsname, code, lineno)

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
            instrs[0:0] = [Instr("MAKE_CELL", bc.CellVar(_), lineno=lineno) for _ in code.co_cellvars]  # type: ignore[attr-defined]

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

    # Carry forward the existing wrapped-function link to the new inner copy.
    with _wrapped_lock:
        existing_inner = _wrapped.get(f)
        if existing_inner is not None:
            _wrapped[wrapped] = existing_inner

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
    wrapped_code.argnames = list(code.co_varnames[:nargs])
    wrapped_code.filename = code.co_filename
    wrapped_code.freevars = list(code.co_freevars)
    wrapped_code.flags = bc.CompilerFlags(flags)
    wrapped_code.kwonlyargcount = kwonlycount
    wrapped_code.name = code.co_name
    wrapped_code.posonlyargcount = code.co_posonlyargcount
    if PY >= (3, 11):
        wrapped_code.cellvars = list(code.co_cellvars)

    # Replace the function code with the trampoline bytecode
    f.__code__ = wrapped_code.to_code()

    # DEV: Multiple wrapping is implemented as a singly-linked list via _wrapped.
    with _wrapped_lock:
        _wrapped[f] = wrapped

    # Link the original code object to the original function
    link_function_to_code(code, f)

    return cast(WrappedFunction, f)


def _unwrap_method(f: Any) -> Any:
    """Return the underlying function if *f* is a bound or unbound method."""
    func = getattr(f, "__func__", f)
    return func


def is_wrapped(f: FunctionType) -> bool:
    """Check if a function is wrapped with any wrapper."""
    try:
        with _wrapped_lock:
            inner = _wrapped.get(_unwrap_method(f))
    except TypeError:
        # f is not weakly referenceable (e.g. C method_descriptor)
        return False
    if inner is None:
        return False
    assert inner.__name__ == "<wrapped>", "Wrapper has wrapped function"  # nosec
    return True


def is_wrapped_with(f: FunctionType, wrapper: Wrapper) -> bool:
    """Check if a function is wrapped with a specific wrapper."""
    f = cast(FunctionType, _unwrap_method(f))
    try:
        with _wrapped_lock:
            inner = _wrapped.get(f)
    except TypeError:
        return False
    if inner is None:
        return False

    assert inner.__name__ == "<wrapped>", "Wrapper has wrapped function"  # nosec

    if wrapper in f.__code__.co_consts:
        return True

    # This is not the correct wrapping layer. Try with the next one.
    return is_wrapped_with(inner, wrapper)


def unwrap(wf: WrappedFunction, wrapper: Wrapper) -> FunctionType:
    """Unwrap a wrapped function.

    This is the reverse of :func:`wrap`. In case of multiple wrapping layers,
    this will unwrap the one that uses ``wrapper``. If the function was not
    wrapped with ``wrapper``, it will return the first argument.
    """
    # DEV: Multiple wrapping layers are singly-linked via _wrapped. When we
    # find the layer that needs to be removed we also have to ensure that we
    # update the link at the deletion site if there is a non-empty tail.
    f = cast(FunctionType, wf)
    with _wrapped_lock:
        inner = _wrapped.get(f)
    if inner is None:
        # The function is not wrapped so we return it as is.
        return f

    assert inner.__name__ == "<wrapped>", "Wrapper has wrapped function"  # nosec

    if wrapper not in f.__code__.co_consts:
        # This is not the correct wrapping layer. Try with the next one.
        return unwrap(cast(WrappedFunction, inner), wrapper)

    # Remove the current wrapping layer by moving the next one over the current
    # one. The code swap and registry update must be atomic: two concurrent
    # unwrap calls on the same function and wrapper must not both succeed.
    with _wrapped_lock:
        f.__code__ = inner.__code__
        next_inner = _wrapped.get(inner)
        if next_inner is not None:
            _wrapped[f] = next_inner
        else:
            del _wrapped[f]

    return f


def get_function_code(f: FunctionType) -> CodeType:
    with _wrapped_lock:
        inner = _wrapped.get(f)
    return (inner if inner is not None else f).__code__


def set_function_code(f: FunctionType, code: CodeType) -> None:
    with _wrapped_lock:
        inner = _wrapped.get(f)
    (inner if inner is not None else f).__code__ = code


def get_wrapped(f: FunctionType) -> Optional[FunctionType]:
    """Return the inner bytecode copy of *f* if it is wrapped, else None."""
    try:
        with _wrapped_lock:
            return _wrapped.get(cast(FunctionType, _unwrap_method(f)))
    except TypeError:
        return None

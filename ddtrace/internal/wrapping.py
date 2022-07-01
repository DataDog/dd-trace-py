import sys
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
    from typing_extensions import Protocol  # type: ignore[misc]

from bytecode import Bytecode
from bytecode import Compare
from bytecode import CompilerFlags
from bytecode import Instr
from bytecode import Label

from .compat import PYTHON_VERSION_INFO as PY


class WrappedFunction(Protocol):
    """A wrapped function."""

    __dd_wrapped__ = None  # type: Optional[FunctionType]
    __dd_wrappers__ = None  # type: Optional[Dict[Any, Any]]

    def __call__(self, *args, **kwargs):
        pass


Wrapper = Callable[[FunctionType, Tuple[Any], Dict[str, Any]], Any]


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

    def compare_exc(label, lineno):
        """Compat helper for comparing exceptions."""
        return (
            Instr("COMPARE_OP", Compare.EXC_MATCH, lineno=lineno)
            if PY < (3, 9)
            else Instr("JUMP_IF_NOT_EXC_MATCH", label, lineno=lineno)
        )

    def jump_if_false(label, lineno):
        """Compat helper for jumping if false after comparing exceptions."""
        return Instr("POP_JUMP_IF_FALSE", label, lineno=lineno) if PY < (3, 9) else Instr("NOP", lineno=lineno)

    def end_finally(lineno):
        """Compat helper for ending finally blocks."""
        if PY < (3, 9):
            return Instr("END_FINALLY", lineno=lineno)
        elif PY < (3, 10):
            return Instr("RERAISE", lineno=lineno)
        return Instr("RERAISE", 0, lineno=lineno)

    code = wrapped.__code__
    lineno = code.co_firstlineno
    varargs = bool(code.co_flags & CompilerFlags.VARARGS)
    varkwargs = bool(code.co_flags & CompilerFlags.VARKEYWORDS)
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

    # Build the tuple of all the positional arguments
    if nargs:
        instrs.extend([Instr("LOAD_FAST", argname, lineno=lineno) for argname in argnames])
        instrs.append(Instr("BUILD_TUPLE", nargs, lineno=lineno))
        if varargs:
            instrs.extend(
                [
                    Instr("LOAD_FAST", varargsname, lineno=lineno),
                    Instr("INPLACE_ADD", lineno=lineno),
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
            instrs.extend(
                [
                    Instr("DUP_TOP", lineno=lineno),
                    Instr("LOAD_ATTR", "update", lineno=lineno),
                    Instr("LOAD_FAST", varkwargsname, lineno=lineno),
                    Instr("CALL_FUNCTION", 1, lineno=lineno),
                    Instr("POP_TOP", lineno=lineno),
                ]
            )

    elif varkwargs:
        instrs.append(Instr("LOAD_FAST", varkwargsname, lineno=lineno))

    else:
        instrs.append(Instr("BUILD_MAP", 0, lineno=lineno))

    # Call the wrapper function with the wrapped function, the positional and
    # keyword arguments, and return the result.
    instrs.extend(
        [
            Instr("CALL_FUNCTION", 3, lineno=lineno),
            Instr("RETURN_VALUE", lineno=lineno),
        ]
    )

    # If the function has special flags set, like the generator, async generator
    # or coroutine, inject unraveling code before the return opcode.
    if CompilerFlags.GENERATOR & code.co_flags and not (CompilerFlags.COROUTINE & code.co_flags):
        stopiter = Label()
        loop = Label()
        genexit = Label()
        exc = Label()
        propagate = Label()

        # DEV: This is roughly equivalent to
        #
        # __ddgen = wrapper(wrapped, args, kwargs)
        # __ddgensend = __ddgen.send
        # try:
        #     value = next(__ddgen)
        # except StopIteration:
        #     return
        # while True:
        #     try:
        #         tosend = yield value
        #     except GeneratorExit:
        #         return __ddgen.close()
        #     except:
        #         return __ddgen.throw(*sys.exc_info())
        #     try:
        #         value = __ddgensend(tosend)
        #     except StopIteration:
        #         return
        #
        instrs[-1:-1] = [
            Instr("DUP_TOP", lineno=lineno),
            Instr("STORE_FAST", "__ddgen", lineno=lineno),
            Instr("LOAD_ATTR", "send", lineno=lineno),
            Instr("STORE_FAST", "__ddgensend", lineno=lineno),
            Instr("LOAD_CONST", next, lineno=lineno),
            Instr("LOAD_FAST", "__ddgen", lineno=lineno),
            loop,
            Instr("SETUP_EXCEPT" if PY < (3, 8) else "SETUP_FINALLY", stopiter, lineno=lineno),
            Instr("CALL_FUNCTION", 1, lineno=lineno),
            Instr("POP_BLOCK", lineno=lineno),
            Instr("SETUP_EXCEPT" if PY < (3, 8) else "SETUP_FINALLY", genexit, lineno=lineno),
            Instr("YIELD_VALUE", lineno=lineno),
            Instr("POP_BLOCK", lineno=lineno),
            Instr("LOAD_FAST", "__ddgensend", lineno=lineno),
            Instr("ROT_TWO", lineno=lineno),
            Instr("JUMP_ABSOLUTE", loop, lineno=lineno),
            stopiter,  # except StpIteration:
            Instr("DUP_TOP", lineno=lineno),
            Instr("LOAD_CONST", StopIteration, lineno=lineno),
            compare_exc(propagate, lineno),
            jump_if_false(propagate, lineno),
            Instr("POP_TOP", lineno=lineno),
            Instr("POP_TOP", lineno=lineno),
            Instr("POP_TOP", lineno=lineno),
            Instr("LOAD_CONST", None, lineno=lineno),
            Instr("RETURN_VALUE", lineno=lineno),
            propagate,
            end_finally(lineno),
            Instr("LOAD_CONST", None, lineno=lineno),
            Instr("RETURN_VALUE", lineno=lineno),
            genexit,  # except GeneratorExit:
            Instr("DUP_TOP", lineno=lineno),
            Instr("LOAD_CONST", GeneratorExit, lineno=lineno),
            compare_exc(exc, lineno),
            jump_if_false(exc, lineno),
            Instr("POP_TOP", lineno=lineno),
            Instr("POP_TOP", lineno=lineno),
            Instr("POP_TOP", lineno=lineno),
            Instr("LOAD_FAST", "__ddgen", lineno=lineno),
            Instr("LOAD_ATTR", "close", lineno=lineno),
            Instr("CALL_FUNCTION", 0, lineno=lineno),
            Instr("RETURN_VALUE", lineno=lineno),
            exc,  # except:
            Instr("POP_TOP", lineno=lineno),
            Instr("POP_TOP", lineno=lineno),
            Instr("POP_TOP", lineno=lineno),
            Instr("LOAD_FAST", "__ddgen", lineno=lineno),
            Instr("LOAD_ATTR", "throw", lineno=lineno),
            Instr("LOAD_CONST", sys.exc_info, lineno=lineno),
            Instr("CALL_FUNCTION", 0, lineno=lineno),
            Instr("CALL_FUNCTION_VAR" if PY < (3, 6) else "CALL_FUNCTION_EX", 0, lineno=lineno),
        ]
    elif PY3:
        if CompilerFlags.COROUTINE & code.co_flags:
            # DEV: This is just
            # >>> return await wrapper(wrapped, args, kwargs)
            instrs[-1:-1] = [
                Instr("GET_AWAITABLE", lineno=lineno),
                Instr("LOAD_CONST", None, lineno=lineno),
                Instr("YIELD_FROM", lineno=lineno),
            ]
        elif CompilerFlags.ASYNC_GENERATOR & code.co_flags:
            stopiter = Label()
            loop = Label()
            genexit = Label()
            exc = Label()
            propagate = Label()

            # DEV: This is roughly equivalent to
            #
            # __ddgen = wrapper(wrapped, args, kwargs)
            # __ddgensend = __ddgen.asend
            # try:
            #     value = await _ddgen.__anext__()
            # except StopAsyncIteration:
            #     return
            # while True:
            #     try:
            #         tosend = yield value
            #     except GeneratorExit:
            #         __ddgen.close()
            #     except:
            #         __ddgen.throw(*sys.exc_info())
            #     try:
            #         value = await __ddgensend(tosend)
            #     except StopAsyncIteration:
            #         return
            #
            instrs[-1:-1] = [
                Instr("DUP_TOP", lineno=lineno),
                Instr("STORE_FAST", "__ddgen", lineno=lineno),
                Instr("LOAD_ATTR", "asend", lineno=lineno),
                Instr("STORE_FAST", "__ddgensend", lineno=lineno),
                Instr("LOAD_FAST", "__ddgen", lineno=lineno),
                Instr("LOAD_ATTR", "__anext__", lineno=lineno),
                Instr("CALL_FUNCTION", 0, lineno=lineno),
                loop,
                Instr("GET_AWAITABLE", lineno=lineno),
                Instr("LOAD_CONST", None, lineno=lineno),
                Instr("SETUP_EXCEPT" if PY < (3, 8) else "SETUP_FINALLY", stopiter, lineno=lineno),
                Instr("YIELD_FROM", lineno=lineno),
                Instr("POP_BLOCK", lineno=lineno),
                Instr("SETUP_EXCEPT" if PY < (3, 8) else "SETUP_FINALLY", genexit, lineno=lineno),
                Instr("YIELD_VALUE", lineno=lineno),
                Instr("POP_BLOCK", lineno=lineno),
                Instr("LOAD_FAST", "__ddgensend", lineno=lineno),
                Instr("ROT_TWO", lineno=lineno),
                Instr("CALL_FUNCTION", 1, lineno=lineno),
                Instr("JUMP_ABSOLUTE", loop, lineno=lineno),
                stopiter,  # except StopAsyncIteration:
                Instr("DUP_TOP", lineno=lineno),
                Instr("LOAD_CONST", StopAsyncIteration, lineno=lineno),
                compare_exc(propagate, lineno),
                jump_if_false(propagate, lineno),
                Instr("POP_TOP", lineno=lineno),
                Instr("POP_TOP", lineno=lineno),
                Instr("POP_TOP", lineno=lineno),
                Instr("LOAD_CONST", None, lineno=lineno),
                Instr("RETURN_VALUE", lineno=lineno),
                propagate,  # finally:
                end_finally(lineno),
                Instr("LOAD_CONST", None, lineno=lineno),
                Instr("RETURN_VALUE", lineno=lineno),
                genexit,  # except GeneratorExit:
                Instr("DUP_TOP", lineno=lineno),
                Instr("LOAD_CONST", GeneratorExit, lineno=lineno),
                compare_exc(exc, lineno),
                jump_if_false(exc, lineno),
                Instr("POP_TOP", lineno=lineno),
                Instr("POP_TOP", lineno=lineno),
                Instr("POP_TOP", lineno=lineno),
                Instr("LOAD_FAST", "__ddgen", lineno=lineno),
                Instr("LOAD_ATTR", "aclose", lineno=lineno),
                Instr("CALL_FUNCTION", 0, lineno=lineno),
                Instr("GET_AWAITABLE", lineno=lineno),
                Instr("LOAD_CONST", None, lineno=lineno),
                Instr("YIELD_FROM", lineno=lineno),
                Instr("POP_EXCEPT", lineno=lineno),
                Instr("RETURN_VALUE", lineno=lineno),
                exc,  # except:
                Instr("POP_TOP", lineno=lineno),
                Instr("POP_TOP", lineno=lineno),
                Instr("POP_TOP", lineno=lineno),
                Instr("LOAD_FAST", "__ddgen", lineno=lineno),
                Instr("LOAD_ATTR", "athrow", lineno=lineno),
                Instr("LOAD_CONST", sys.exc_info, lineno=lineno),
                Instr("CALL_FUNCTION", 0, lineno=lineno),
                Instr("CALL_FUNCTION_EX", 0, lineno=lineno),
                Instr("GET_AWAITABLE", lineno=lineno),
                Instr("LOAD_CONST", None, lineno=lineno),
                Instr("YIELD_FROM", lineno=lineno),
                Instr("POP_EXCEPT", lineno=lineno),
            ]

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
    nargs += bool(code.flags & CompilerFlags.VARARGS) + bool(code.flags & CompilerFlags.VARKEYWORDS)
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
        assert inner.__name__ == "<wrapped>", "Wrapper has wrapped function"

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

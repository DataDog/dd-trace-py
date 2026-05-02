import sys
from types import CodeType
from types import FunctionType
from typing import Any
from typing import Callable
from typing import Optional
from typing import Protocol
from typing import cast

from ddtrace.internal.utils.inspection import link_function_to_code


PY = sys.version_info[:2]


class WrappedFunction(Protocol):
    """A wrapped function."""

    __dd_wrapped__: Optional[FunctionType] = None
    __dd_wrappers__: Optional[dict[Any, Any]] = None

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        pass


Wrapper = Callable[[FunctionType, tuple[Any], dict[str, Any]], Any]


# Two implementations of `wrap()` ship in this module:
#   * 3.15+: pure-Python closure trampoline (no `bytecode` lib dependency).
#     Eliminates the need for the version-specific bytecode in
#     `wrapping/asyncs.py` and `wrapping/generators.py`. The Python compiler
#     emits the right generator/coroutine bytecode for the trampolines.
#   * 3.9-3.14: bytecode rewriting via the `bytecode` lib. Unchanged.
#
# Both expose identical public API:
#   * wrap
#   * unwrap
#   * is_wrapped
#   * is_wrapped_with
#   * get_function_code
#   * set_function_code


if PY >= (3, 15):
    # ----------------------------------------------------------------------
    # Closure trampoline path (no bytecode lib).
    # ----------------------------------------------------------------------
    # The trampoline is generated via exec() with the *exact*
    # signature of the original function. Python's argument binding then
    # normalizes positional / keyword / *args / **kwargs / defaults the
    # same way it would for the original. Inside the trampoline body we
    # collect named locals into `args` and `kwargs` and call
    # `wrapper(inner, args, kwargs)`. This matches the bytecode path's
    # contract: the wrapper *always* sees a canonical (args, kwargs)
    # split where positional params (incl. those bound from kwargs or
    # defaults) live in `args`, and only **varkwargs values land in
    # `kwargs` (alongside keyword-only params if any).
    import ctypes
    from inspect import CO_ASYNC_GENERATOR
    from inspect import CO_COROUTINE
    from inspect import CO_GENERATOR
    from inspect import CO_VARARGS
    from inspect import CO_VARKEYWORDS

    # We must atomically swap (`__code__`, `__closure__`) on
    # the wrapped function. The Python-level setters validate that
    # `len(closure) == co_freevars` *at every step*, so a two-step
    # assignment fails. The C-level `PyFunction_SetClosure` does NOT
    # validate, letting us set the closure tuple first and then assign
    # `__code__` (whose validation now passes against the freshly-set
    # closure). This is a documented public C API; see
    # https://docs.python.org/3/c-api/function.html.
    _PyFunction_SetClosure = ctypes.pythonapi.PyFunction_SetClosure
    _PyFunction_SetClosure.argtypes = [ctypes.py_object, ctypes.py_object]
    _PyFunction_SetClosure.restype = ctypes.c_int

    _SENTINEL: object = object()

    def _swap_code_and_closure(f: FunctionType, code: CodeType, closure: Optional[tuple]) -> None:
        """Atomically replace f's __code__ and __closure__.

        Python-level setters validate co_freevars against closure length
        at each step; we use the C-level setter to skip validation, then
        assign __code__ (whose validation matches the just-set closure).
        """
        rc = _PyFunction_SetClosure(f, closure if closure is not None else ())
        if rc != 0:
            raise SystemError("PyFunction_SetClosure failed")
        f.__code__ = code

    def _make_inner(f: FunctionType) -> FunctionType:
        """Create an inner function that runs ``f``'s original body."""
        inner = FunctionType(
            f.__code__,
            f.__globals__,
            "<wrapped>",
            f.__defaults__,
            f.__closure__,
        )
        inner.__kwdefaults__ = f.__kwdefaults__
        inner.__qualname__ = f.__qualname__
        return inner

    _ASYNC_GEN_BODY_TEMPLATE: str = (
        "    {iter} = {wrapper}({inner}, {args}, {kwargs})\n"
        "    try:\n"
        "        {v} = await {iter}.__anext__()\n"
        "    except StopAsyncIteration:\n"
        "        return\n"
        "    while True:\n"
        "        try:\n"
        "            {sent} = yield {v}\n"
        "        except GeneratorExit:\n"
        "            await {iter}.aclose()\n"
        "            raise\n"
        "        except BaseException as {exc}:\n"
        "            try:\n"
        "                {v} = await {iter}.athrow(type({exc}), {exc}, {exc}.__traceback__)\n"
        "            except StopAsyncIteration:\n"
        "                return\n"
        "        else:\n"
        "            try:\n"
        "                {v} = await {iter}.asend({sent})\n"
        "            except StopAsyncIteration:\n"
        "                return\n"
    )

    def _build_trampoline(f: FunctionType, wrapper: Wrapper, inner: FunctionType) -> FunctionType:
        """Build a trampoline whose signature mirrors ``f`` exactly.

        Generated source (regular function, illustrative):

            def __dd_factory(__dd_w, __dd_i):
                def __dd_trampoline(<f-signature>):
                    return __dd_w(__dd_i, <pos-args-tuple>, <kw-args-dict>)
                return __dd_trampoline
        """
        code: CodeType = f.__code__
        flags: int = code.co_flags
        n_args: int = code.co_argcount
        n_posonly: int = code.co_posonlyargcount
        n_kwonly: int = code.co_kwonlyargcount
        has_varargs: bool = bool(flags & CO_VARARGS)
        has_varkwargs: bool = bool(flags & CO_VARKEYWORDS)
        is_coro: bool = bool(flags & CO_COROUTINE)
        is_async_gen: bool = bool(flags & CO_ASYNC_GENERATOR)
        is_gen: bool = bool(flags & CO_GENERATOR) and not is_coro and not is_async_gen

        varnames: tuple[str, ...] = code.co_varnames
        n_sig: int = n_args + n_kwonly + has_varargs + has_varkwargs
        for name in varnames[:n_sig]:
            if not name.isidentifier():
                raise ValueError(f"Parameter name {name!r} is not a valid Python identifier")
        pos_argnames: list[str] = list(varnames[:n_args])
        kwonly_argnames: list[str] = list(varnames[n_args : n_args + n_kwonly])
        varargs_name: str = varnames[n_args + n_kwonly] if has_varargs else ""
        varkwargs_name: str = varnames[n_args + n_kwonly + (1 if has_varargs else 0)] if has_varkwargs else ""

        defaults: tuple[Any, ...] = f.__defaults__ or ()
        kwdefaults: dict[str, Any] = f.__kwdefaults__ or {}

        # Reserve unique sentinel names that won't collide with f's params.
        used: set[str] = set(pos_argnames) | set(kwonly_argnames)
        if varargs_name:
            used.add(varargs_name)
        if varkwargs_name:
            used.add(varkwargs_name)

        def _uniq(prefix: str) -> str:
            n: int = 0
            cand: str = "__dd_" + prefix
            while cand in used:
                n += 1
                cand = "__dd_" + prefix + "_" + str(n)
            used.add(cand)
            return cand

        wrapper_n: str = _uniq("w")
        inner_n: str = _uniq("i")
        default_ns: list[str] = [_uniq("d" + str(i)) for i in range(len(defaults))]
        kwdefault_ns: dict[str, str] = {name: _uniq("kd_" + name) for name in kwdefaults}

        # Reconstruct the signature string with default placeholders.
        sig_parts: list[str] = []
        n_defaults: int = len(defaults)
        for i, name in enumerate(pos_argnames):
            if i >= n_args - n_defaults:
                sig_parts.append(name + "=" + default_ns[i - (n_args - n_defaults)])
            else:
                sig_parts.append(name)
            if i == n_posonly - 1 and n_posonly > 0:
                sig_parts.append("/")
        if has_varargs:
            sig_parts.append("*" + varargs_name)
        elif kwonly_argnames:
            sig_parts.append("*")
        for name in kwonly_argnames:
            if name in kwdefaults:
                sig_parts.append(name + "=" + kwdefault_ns[name])
            else:
                sig_parts.append(name)
        if has_varkwargs:
            sig_parts.append("**" + varkwargs_name)
        signature: str = ", ".join(sig_parts)

        # Build the args tuple expression that mirrors the bytecode path's
        # `generate_posargs`: load each positional local then concat *varargs.
        args_expr: str
        if pos_argnames:
            pos_tuple: str = "(" + ", ".join(pos_argnames) + ("," if len(pos_argnames) == 1 else "") + ")"
            args_expr = pos_tuple + (" + " + varargs_name if has_varargs else "")
        elif has_varargs:
            args_expr = varargs_name
        else:
            args_expr = "()"

        # Build the kwargs dict expression that mirrors `generate_kwargs`:
        # kwonly params land in the dict (by name), then **varkwargs is merged.
        kwargs_expr: str
        if kwonly_argnames:
            kw_pairs: str = ", ".join("'" + n + "': " + n for n in kwonly_argnames)
            kwargs_expr = "{" + kw_pairs + (", **" + varkwargs_name if has_varkwargs else "") + "}"
        elif has_varkwargs:
            kwargs_expr = varkwargs_name
        else:
            kwargs_expr = "{}"

        # Build the body.
        async_kw: str
        body: str
        if is_coro:
            async_kw = "async "
            body = "    return await " + wrapper_n + "(" + inner_n + ", " + args_expr + ", " + kwargs_expr + ")\n"
        elif is_async_gen:
            async_kw = "async "
            body = _ASYNC_GEN_BODY_TEMPLATE.format(
                iter=_uniq("iter"),
                v=_uniq("v"),
                sent=_uniq("sent"),
                exc=_uniq("exc"),
                wrapper=wrapper_n,
                inner=inner_n,
                args=args_expr,
                kwargs=kwargs_expr,
            )
        elif is_gen:
            async_kw = ""
            body = "    yield from " + wrapper_n + "(" + inner_n + ", " + args_expr + ", " + kwargs_expr + ")\n"
        else:
            async_kw = ""
            body = "    return " + wrapper_n + "(" + inner_n + ", " + args_expr + ", " + kwargs_expr + ")\n"

        factory_params: list[str] = [wrapper_n, inner_n] + default_ns + [kwdefault_ns[n] for n in kwdefaults]
        factory_args: list[Any] = [wrapper, inner] + list(defaults) + [kwdefaults[n] for n in kwdefaults]

        inner_body: str = "".join("    " + ln if ln.strip() else ln for ln in body.splitlines(keepends=True))

        src: str = (
            "def __dd_factory(" + ", ".join(factory_params) + "):\n"
            "    "
            + async_kw
            + "def __dd_trampoline("
            + signature
            + "):\n"
            + inner_body
            + "    return __dd_trampoline\n"
        )

        ns: dict[str, Any] = {}
        # `src` is fully constructed from f.__code__ metadata
        # (parameter names, defaults) — no user-controlled input. The exec'd
        # source defines a single factory function that returns the
        # trampoline; the wrapper and inner are passed in as factory args
        # (closure cells), not embedded as text. Equivalent in trust to
        # constructing a function with `types.FunctionType`.
        exec(src, ns)  # nosec B102
        return ns["__dd_factory"](*factory_args)

    def _rename_code(code: CodeType, original: CodeType) -> CodeType:
        """Rename trampoline's code object so call stacks/tracebacks read as the original."""
        # `co_qualname` exists since 3.11 and is always available
        # in this branch (PY >= (3, 15)). mypy type-checks against the lowest
        # supported Python (3.10) typeshed where `co_qualname` is absent, so
        # we silence the call-arg / attr-defined errors here.
        return code.replace(  # type: ignore[call-arg]
            co_name=original.co_name,
            co_qualname=original.co_qualname,  # type: ignore[attr-defined]
            co_filename=original.co_filename,
            co_firstlineno=original.co_firstlineno,
        )

    def wrap(f: FunctionType, wrapper: Wrapper) -> WrappedFunction:
        """Wrap a function with a wrapper, preserving f's identity.

        After ``wrap(f, wrapper)``, calling ``f(*a, **kw)`` is equivalent
        to ``wrapper(<wrapped>, a, kw)`` where ``<wrapped>`` retains f's
        original body. Multiple wraps stack via the ``__dd_wrapped__``
        singly-linked list (matches 3.9-3.14 contract).
        """
        original_code: CodeType = f.__code__
        inner: FunctionType = _make_inner(f)

        # Preserve any prior __dd_wrapped__/__dd_wrapper__ chain so multiple wraps stack.
        for attr in ("__dd_wrapped__", "__dd_wrapper__"):
            val: object = getattr(f, attr, _SENTINEL)
            if val is not _SENTINEL:
                setattr(inner, attr, val)

        trampoline: FunctionType = _build_trampoline(f, wrapper, inner)

        # Replace f's code/closure with the trampoline's. Keep f's
        # name/module/qualname/etc. The renamed code object makes call
        # stacks show the original function name.
        _swap_code_and_closure(
            f,
            _rename_code(trampoline.__code__, original_code),
            trampoline.__closure__,
        )
        f.__defaults__ = trampoline.__defaults__
        f.__kwdefaults__ = trampoline.__kwdefaults__

        wf: WrappedFunction = cast(WrappedFunction, f)
        wf.__dd_wrapped__ = inner
        # __dd_wrapper__ identifies this layer's wrapper for
        # is_wrapped_with / unwrap. The 3.9-3.14 bytecode path uses
        # f.__code__.co_consts instead; the public API is identical.
        cast(Any, f).__dd_wrapper__ = wrapper

        # Mirror the bytecode path: keep a reverse map from the ORIGINAL
        # code object to the outer wrapped function. Downstream callers
        # (debuggers, error tracking, etc.) need this lookup to find the
        # function from a frame's f_code.
        link_function_to_code(original_code, f)

        return wf

    def is_wrapped(f: FunctionType) -> bool:
        """Check if a function is wrapped with any wrapper."""
        inner = getattr(f, "__dd_wrapped__", None)
        return inner is not None

    def is_wrapped_with(f: FunctionType, wrapper: Wrapper) -> bool:
        """Check if a function is wrapped with a specific wrapper."""
        inner = getattr(f, "__dd_wrapped__", None)
        if inner is None or getattr(inner, "__name__", None) != "<wrapped>":
            return False

        if getattr(f, "__dd_wrapper__", None) is wrapper:
            return True

        return is_wrapped_with(inner, wrapper)

    def unwrap(wf: WrappedFunction, wrapper: Wrapper) -> FunctionType:
        """Unwrap a wrapped function.

        Reverse of :func:`wrap`. With multiple layers, unwraps the layer
        that uses ``wrapper``. If not wrapped with ``wrapper``, returns
        the first argument.
        """
        inner: Optional[FunctionType] = getattr(wf, "__dd_wrapped__", None)
        if inner is None or getattr(inner, "__name__", None) != "<wrapped>":
            return cast(FunctionType, wf)

        if getattr(wf, "__dd_wrapper__", None) is not wrapper:
            return unwrap(cast(WrappedFunction, inner), wrapper)

        # Remove this layer by overlaying inner onto wf.
        f: FunctionType = cast(FunctionType, wf)
        _swap_code_and_closure(f, inner.__code__, inner.__closure__)
        f.__defaults__ = inner.__defaults__
        f.__kwdefaults__ = inner.__kwdefaults__

        for attr in ("__dd_wrapped__", "__dd_wrapper__"):
            next_val: object = getattr(inner, attr, _SENTINEL)
            if next_val is not _SENTINEL:
                setattr(wf, attr, next_val)
            elif hasattr(wf, attr):
                delattr(wf, attr)

        return f

    def get_function_code(f: FunctionType) -> CodeType:
        return (cast(WrappedFunction, f).__dd_wrapped__ or f if is_wrapped(f) else f).__code__

    def set_function_code(f: FunctionType, code: CodeType) -> None:
        (cast(WrappedFunction, f).__dd_wrapped__ or f if is_wrapped(f) else f).__code__ = code  # type: ignore[misc]

else:
    # ----------------------------------------------------------------------
    # 3.9-3.14 bytecode-rewriting path. Unchanged from prior implementation.
    # ----------------------------------------------------------------------
    from typing import Generator

    import bytecode as bc
    from bytecode import Instr

    from ddtrace.internal.assembly import Assembly
    from ddtrace.internal.wrapping.asyncs import wrap_async
    from ddtrace.internal.wrapping.generators import wrap_generator

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

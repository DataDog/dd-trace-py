from collections import deque
from types import CodeType
from types import FunctionType
from typing import Any  # noqa:F401
from typing import Callable  # noqa:F401

from bytecode import Bytecode
from bytecode import Instr

from ddtrace.internal.assembly import Assembly
from ddtrace.internal.compat import PYTHON_VERSION_INFO as PY
from ddtrace.internal.wrapping import get_function_code
from ddtrace.internal.wrapping import set_function_code


HookType = Callable[[Any], Any]
HookInfoType = tuple[HookType, int, Any]

HOOK_ARG_PREFIX = "_hook_arg"


class InvalidLine(Exception):
    """
    Raised when trying to inject a hook on an invalid line, e.g. a comment or a blank line.
    """


if PY >= (3, 15):
    import weakref

    from ddtrace.internal import monitoring as _monitoring
    from ddtrace.internal.threads import Lock
    from ddtrace.internal.utils.inspection import linenos

    class _LineHookHandler(_monitoring.MonitoringEventHandler):
        """Per-code-object handler that dispatches line hooks via sys.monitoring."""

        def __init__(self) -> None:
            # lineno -> list of (hook, arg) pairs in registration order
            self._hooks: dict[int, list[tuple[HookType, Any]]] = {}

        def on_py_line(self, code: Any, line_number: int) -> Any:
            hooks: "list[tuple[HookType, Any]] | None" = self._hooks.get(line_number)
            if not hooks:
                return _monitoring.DISABLE  # type: ignore[has-type]
            for hook, arg in hooks:
                hook(arg)
            return None

        def add(self, line: int, hook: HookType, arg: Any) -> None:
            self._hooks.setdefault(line, []).append((hook, arg))

        def remove(self, line: int, hook: HookType, arg: Any) -> None:
            hooks: "list[tuple[HookType, Any]] | None" = self._hooks.get(line)
            if hooks is not None:
                try:
                    hooks.remove((hook, arg))
                except ValueError:
                    pass
                if not hooks:
                    del self._hooks[line]

        @property
        def is_empty(self) -> bool:
            return not self._hooks

    # WeakKeyDictionary: code object -> _LineHookHandler
    _line_hook_registry: "weakref.WeakKeyDictionary[CodeType, _LineHookHandler]" = weakref.WeakKeyDictionary()
    _line_hook_lock = Lock()

    def inject_hooks(f: FunctionType, hooks: list[HookInfoType]) -> list[HookInfoType]:
        """Bulk-inject a list of hooks into a function.

        Hooks are specified via a list of tuples, where each tuple contains the hook
        itself, the line number and the identifying argument passed to the hook.

        Returns the list of hooks that failed to be injected.
        """
        code: CodeType = get_function_code(f)
        valid_lines: set[int] = linenos(code)
        failed: list[HookInfoType] = []

        with _line_hook_lock:
            handler: _LineHookHandler | None = _line_hook_registry.get(code)
            if handler is None:
                handler = _LineHookHandler()
                new_handler: bool = True
            else:
                new_handler = False

            for hook, line, arg in hooks:
                if line not in valid_lines:
                    failed.append((hook, line, arg))
                    continue
                handler.add(line, hook, arg)

            if not handler.is_empty:
                if new_handler:
                    _line_hook_registry[code] = handler
                    _monitoring.register(code, handler)
                else:
                    # Reset any lines that were DISABLE'd so newly added hooks fire.
                    _monitoring.refresh(code)

        return failed

    def migrate_line_hooks(src: CodeType, dst: CodeType) -> None:
        """Move line-hook registration from *src* to *dst*.

        Wrapping on 3.15 clones the function's code object; line probes installed
        on the pre-wrap code must follow the clone or they stop firing.
        """
        with _line_hook_lock:
            handler: _LineHookHandler | None = _line_hook_registry.get(src)
            if handler is None:
                return
            del _line_hook_registry[src]
            _monitoring.unregister(src, handler)
            _line_hook_registry[dst] = handler
            _monitoring.register(dst, handler)

    def eject_all_hooks(f: FunctionType) -> None:
        """Remove every line hook registered for *f*."""
        code: CodeType = get_function_code(f)
        with _line_hook_lock:
            handler: _LineHookHandler | None = _line_hook_registry.get(code)
            if handler is None:
                return
            del _line_hook_registry[code]
            _monitoring.unregister(code, handler)

    def eject_hooks(f: FunctionType, hooks: list[HookInfoType]) -> list[HookInfoType]:
        """Bulk-eject a list of hooks from a function.

        The hooks are specified via a list of tuples, where each tuple contains the
        hook line number and the identifying argument.

        Returns the list of hooks that failed to be ejected.
        """
        code: CodeType = get_function_code(f)
        failed: list[HookInfoType] = []

        with _line_hook_lock:
            handler: _LineHookHandler | None = _line_hook_registry.get(code)
            if handler is None:
                return list(hooks)

            for hook, line, arg in hooks:
                before: int = len(handler._hooks.get(line, ()))
                handler.remove(line, hook, arg)
                if len(handler._hooks.get(line, ())) == before:
                    failed.append((hook, line, arg))

            if handler.is_empty:
                del _line_hook_registry[code]
                _monitoring.unregister(code, handler)

        return failed

    def inject_hook(f: FunctionType, hook: HookType, line: int, arg: Any) -> FunctionType:
        """Inject a hook into a function at the given line number."""
        failed: list[HookInfoType] = inject_hooks(f, [(hook, line, arg)])
        if failed:
            raise InvalidLine("Line %d does not exist or is either blank or a comment" % line)
        return f

    def eject_hook(f: FunctionType, hook: HookType, line: int, arg: Any) -> FunctionType:
        """Eject a hook from a function at the given line number."""
        failed: list[HookInfoType] = eject_hooks(f, [(hook, line, arg)])
        if failed:
            raise InvalidLine("Line %d does not contain a hook" % line)
        return f

else:
    # DEV: This is the bytecode equivalent of
    # >>> hook(arg)
    # Additionally, we must discard the return value (top of the stack) to restore
    # the stack to the state prior to the call.

    INJECTION_ASSEMBLY = Assembly()
    if PY >= (3, 13):
        INJECTION_ASSEMBLY.parse(
            r"""
            load_const      {hook}
            push_null
            load_const      {arg}
            call            1
            pop_top
            """
        )
    elif PY >= (3, 12):
        INJECTION_ASSEMBLY.parse(
            r"""
            push_null
            load_const      {hook}
            load_const      {arg}
            call            1
            pop_top
            """
        )
    elif PY >= (3, 11):
        INJECTION_ASSEMBLY.parse(
            r"""
            push_null
            load_const      {hook}
            load_const      {arg}
            precall         1
            call            1
            pop_top
            """
        )
    else:
        INJECTION_ASSEMBLY.parse(
            r"""
            load_const      {hook}
            load_const      {arg}
            call_function   1
            pop_top
            """
        )

    _INJECT_HOOK_OPCODES = [_.name for _ in INJECTION_ASSEMBLY]

    def _inject_hook(code: Bytecode, hook: HookType, lineno: int, arg: Any) -> None:
        """Inject a hook at the given line number inside an abstract code object.

        The hook is called with the given argument, which is also used as an
        identifier for the hook itself. This should be kept in case the hook needs
        to be removed.
        """
        # DEV: In general there are no guarantees for bytecode to be "linear",
        # meaning that a line number can occur multiple times. We need to find all
        # occurrences and inject the hook at each of them. An example of when this
        # happens is with finally blocks, which are duplicated at the end of the
        # bytecode.
        locs: deque[tuple[int, str]] = deque()
        last_lineno: int | None = None
        instrs: set[str] = set()
        for i, item in enumerate(code):
            if not isinstance(item, Instr):
                continue
            if item.lineno == last_lineno:
                continue
            last_lineno = item.lineno
            # Some lines might be implemented across multiple instruction
            # offsets, and sometimes a NOP is used as a placeholder. We skip
            # those to avoid duplicate injections.
            if item.lineno == lineno:
                locs.appendleft((i, item.name))
                instrs.add(item.name)

        if not locs:
            raise InvalidLine("Line %d does not exist or is either blank or a comment" % lineno)

        if instrs == {"NOP"}:
            # If the line occurs on NOPs only, we instrument only the first one
            last_instr: tuple[int, str] = locs.pop()
            locs.clear()
            locs.append(last_instr)
        elif "NOP" in instrs:
            # If the line occurs on NOPs and other instructions, we remove the NOPs
            # to avoid injecting the hook multiple times. The NOP in this case is
            # just a placeholder.
            locs = deque((i, instr) for i, instr in locs if instr != "NOP")

        for i, instr in locs:
            if instr.startswith("END_"):
                # This is the end of a block, e.g. a for loop. We have already
                # instrumented the block on entry, so we skip instrumenting the
                # end as well.
                continue
            code[i:i] = INJECTION_ASSEMBLY.bind(dict(hook=hook, arg=arg), lineno=lineno)

    _INJECT_HOOK_OPCODE_POS = 1 if (3, 11) <= PY < (3, 13) else 0
    _INJECT_ARG_OPCODE_POS = 1 if PY < (3, 11) else 2

    def _eject_hook(code: Bytecode, hook: HookType, line: int, arg: Any) -> None:
        """Eject a hook from the abstract code object at the given line number.

        The hook is identified by its argument. This ensures that only the right
        hook is ejected.
        """
        locs: deque[int] = deque()
        for i, item in enumerate(code):
            if not isinstance(item, Instr):
                continue
            try:
                hook_op: object = code[i + _INJECT_HOOK_OPCODE_POS]
                arg_op: object = code[i + _INJECT_ARG_OPCODE_POS]
                if not isinstance(hook_op, Instr) or not isinstance(arg_op, Instr):
                    continue
                opcodes: list[str] = []
                for j in range(i, i + len(_INJECT_HOOK_OPCODES)):
                    op = code[j]
                    if not isinstance(op, Instr):
                        break
                    opcodes.append(op.name)
                else:
                    # DEV: We look at the expected opcode pattern to match the injected
                    # hook and we also test for the expected opcode arguments
                    if (
                        item.lineno == line
                        and hook_op.arg == hook  # bound methods don't like identity comparisons
                        and arg_op.arg is arg
                        and opcodes == _INJECT_HOOK_OPCODES
                    ):
                        locs.appendleft(i)
            except IndexError:
                pass

        if not locs:
            raise InvalidLine("Line %d does not contain a hook" % line)

        for i in locs:
            del code[i : i + len(_INJECT_HOOK_OPCODES)]

    def inject_hooks(f: FunctionType, hooks: list[HookInfoType]) -> list[HookInfoType]:
        """Bulk-inject a list of hooks into a function.

        Hooks are specified via a list of tuples, where each tuple contains the hook
        itself, the line number and the identifying argument passed to the hook.

        Returns the list of hooks that failed to be injected.
        """
        abstract_code: Bytecode = Bytecode.from_code(get_function_code(f))

        failed: list[HookInfoType] = []
        for hook, line, arg in hooks:
            try:
                _inject_hook(abstract_code, hook, line, arg)
            except InvalidLine:
                failed.append((hook, line, arg))

        if len(failed) < len(hooks):
            set_function_code(f, abstract_code.to_code())

        return failed

    def eject_hooks(f: FunctionType, hooks: list[HookInfoType]) -> list[HookInfoType]:
        """Bulk-eject a list of hooks from a function.

        The hooks are specified via a list of tuples, where each tuple contains the
        hook line number and the identifying argument.

        Returns the list of hooks that failed to be ejected.
        """
        abstract_code: Bytecode = Bytecode.from_code(f.__code__)

        failed: list[HookInfoType] = []
        for hook, line, arg in hooks:
            try:
                _eject_hook(abstract_code, hook, line, arg)
            except InvalidLine:
                failed.append((hook, line, arg))

        if len(failed) < len(hooks):
            f.__code__ = abstract_code.to_code()

        return failed

    def inject_hook(f: FunctionType, hook: HookType, line: int, arg: Any) -> FunctionType:
        """Inject a hook into a function.

        The hook is injected at the given line number and called with the given
        argument. The latter is also used as an identifier for the hook. This should
        be kept in case the hook needs to be removed.
        """
        abstract_code: Bytecode = Bytecode.from_code(f.__code__)

        _inject_hook(abstract_code, hook, line, arg)

        f.__code__ = abstract_code.to_code()

        return f

    def eject_hook(f: FunctionType, hook: HookType, line: int, arg: Any) -> FunctionType:
        """Eject a hook from a function.

        The hook is identified by its line number and the argument passed to the
        hook.
        """
        abstract_code: Bytecode = Bytecode.from_code(f.__code__)

        _eject_hook(abstract_code, hook, line, arg)

        f.__code__ = abstract_code.to_code()

        return f

    def eject_all_hooks(f: FunctionType) -> None:
        """No-op on Python <3.15: line hooks are removed when __code__ is restored."""
        return None

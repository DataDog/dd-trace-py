from ddtrace.appsec._iast._logs import iast_compiling_debug_log
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config

from ._ast.ast_patching import astpatch_module


log = get_logger(__name__)

IS_IAST_ENABLED = asm_config._iast_enabled


def _exec_iast_patched_module(module_watchdog, module):
    """Execute a Python module with IAST (Interactive Application Security Testing) instrumentation.

    This function performs dynamic code transformation using AST (Abstract Syntax Tree) patching
    to inject security vulnerability detection capabilities into Python modules at runtime.
    It's a core component of the IAST engine that enables taint tracking and vulnerability
    detection without modifying the original source code.

    How it works:
    1. Attempts to patch the module's AST using astpatch_module()
    2. If successful, compiles the patched AST into executable bytecode
    3. Executes the instrumented bytecode instead of the original module
    4. Falls back to executing the original module if patching fails

    Runtime Considerations:
    - The AST analysis could yield unexpected or incorrect results when analyzing
      code that overwrites built-in or global names at runtime
    - A notable example is `mysqlsh` (MySQL Shell), which reassigns `globals` with
      something like: `globals = ShellGlobals()`. Since `globals` is a built-in
      function in Python, reassigning it alters the global namespace's behavior
      during analysis. This can cause dynamic instrumentation, taint tracking,
      or symbol resolution to behave incorrectly or inconsistently
    - The function gracefully handles compilation and execution errors by falling
      back to the original module execution
    - All exceptions during patching are logged but don't prevent module execution
    """

    patched_ast = None
    compiled_code = None
    if IS_IAST_ENABLED:
        try:
            module_path, patched_ast = astpatch_module(module)
        except Exception:
            iast_compiling_debug_log("Unexpected exception while AST patching", exc_info=True)
            patched_ast = None
    if patched_ast:
        try:
            # Patched source is compiled in order to execute it
            compiled_code = compile(patched_ast, module_path, "exec")
        except Exception:
            iast_compiling_debug_log("Unexpected exception while compiling patched code", exc_info=True)
            compiled_code = None

    if compiled_code:
        iast_compiling_debug_log(f"INSTRUMENTED CODE. executing {module_path}")
        try:
            # Patched source is executed instead of original module
            exec(compiled_code, module.__dict__)  # nosec B102
        except TypeError:
            iast_compiling_debug_log("INSTRUMENTED CODE. Unexpected exception", exc_info=True)
            module_watchdog.loader.exec_module(module)
    elif module_watchdog.loader is not None:
        try:
            iast_compiling_debug_log(f"DEFAULT CODE. executing {module}")
            module_watchdog.loader.exec_module(module)
        except ImportError:
            iast_compiling_debug_log("Unexpected exception on import loader fallback", exc_info=True)
    else:
        iast_compiling_debug_log(f"Module loader is not available, cannot execute module {module}")

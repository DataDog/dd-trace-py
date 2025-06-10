import collections
from dataclasses import dataclass
from fnmatch import fnmatch
import os
import re
import shlex
from shlex import join
from typing import Callable  # noqa:F401
from typing import Deque  # noqa:F401
from typing import Dict  # noqa:F401
from typing import List  # noqa:F401
from typing import Optional  # noqa:F401
from typing import Tuple  # noqa:F401
from typing import Union  # noqa:F401
from typing import cast  # noqa:F401

from ddtrace._trace.pin import Pin
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.subprocess.constants import COMMANDS
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.forksafe import RLock
from ddtrace.internal.logger import get_logger
from ddtrace.settings._config import config
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)

config._add(
    "subprocess",
    dict(sensitive_wildcards=os.getenv("DD_SUBPROCESS_SENSITIVE_WILDCARDS", default="").split(",")),
)


def get_version() -> str:
    """Get the version string for the subprocess integration."""
    return ""


_STR_CALLBACKS: Dict[str, Callable[[str], None]] = {}
_LST_CALLBACKS: Dict[str, Callable[[Union[List[str], str]], None]] = {}


def add_str_callback(name: str, callback: Callable[[str], None]):
    """Add a callback function for string commands.

    Args:
        name: Unique identifier for the callback
        callback: Function that will be called with string command arguments
    """
    _STR_CALLBACKS[name] = callback


def del_str_callback(name: str):
    """Remove a string command callback.

    Args:
        name: Identifier of the callback to remove
    """
    _STR_CALLBACKS.pop(name, None)


def add_lst_callback(name: str, callback: Callable[[Union[List[str], str]], None]):
    """Add a callback function for list commands.

    Args:
        name: Unique identifier for the callback
        callback: Function that will be called with list/tuple command arguments
    """
    _LST_CALLBACKS[name] = callback


def del_lst_callback(name: str):
    """Remove a list command callback.

    Args:
        name: Identifier of the callback to remove
    """
    _LST_CALLBACKS.pop(name, None)


def patch() -> List[str]:
    """Patch subprocess and os functions to enable security monitoring.

    This function instruments various subprocess and os functions to provide
    security monitoring capabilities for AAP (Application Attack Protection)
    and IAST (Interactive Application Security Testing).

    Note:
        Patching always occurs because AAP can be enabled dynamically via remote config.
        Already patched functions are skipped.
    """
    patched: List[str] = []

    import os  # nosec
    import subprocess  # nosec

    should_patch_system = not trace_utils.iswrapped(os.system)
    should_patch_fork = (not trace_utils.iswrapped(os.fork)) if hasattr(os, "fork") else False
    spawnvef = getattr(os, "_spawnvef", None)
    should_patch_spawnvef = spawnvef is not None and not trace_utils.iswrapped(spawnvef)

    if should_patch_system or should_patch_fork or should_patch_spawnvef:
        Pin().onto(os)
        if should_patch_system:
            trace_utils.wrap(os, "system", _traced_ossystem(os))
        if should_patch_fork:
            trace_utils.wrap(os, "fork", _traced_fork(os))
        if should_patch_spawnvef:
            # all os.spawn* variants eventually use this one:
            trace_utils.wrap(os, "_spawnvef", _traced_osspawn(os))
        patched.append("os")

    should_patch_Popen_init = not trace_utils.iswrapped(subprocess.Popen.__init__)
    should_patch_Popen_wait = not trace_utils.iswrapped(subprocess.Popen.wait)
    if should_patch_Popen_init or should_patch_Popen_wait:
        Pin().onto(subprocess)
        # We store the parameters on __init__ in the context and set the tags on wait
        # (where all the Popen objects eventually arrive, unless killed before it)
        if should_patch_Popen_init:
            trace_utils.wrap(subprocess, "Popen.__init__", _traced_subprocess_init(subprocess))
        if should_patch_Popen_wait:
            trace_utils.wrap(subprocess, "Popen.wait", _traced_subprocess_wait(subprocess))
        patched.append("subprocess")

    return patched


@dataclass(eq=False)
class SubprocessCmdLineCacheEntry:
    """Cache entry for storing parsed subprocess command line data.

    This class stores the parsed and processed command line arguments,
    environment variables, and metadata to avoid recomputing the same
    command line parsing multiple times.
    """

    binary: Optional[str] = None
    arguments: Optional[List] = None
    truncated: bool = False
    env_vars: Optional[List] = None
    as_list: Optional[List] = None
    as_string: Optional[str] = None


class SubprocessCmdLine:
    """Parser and scrubber for subprocess command lines.

    This class handles parsing, scrubbing, and caching of subprocess command lines
    for security monitoring. It supports both shell and non-shell commands,
    scrubs sensitive information, and provides caching for performance.
    """

    # This catches the computed values into a SubprocessCmdLineCacheEntry object
    _CACHE: Dict[str, SubprocessCmdLineCacheEntry] = {}
    _CACHE_DEQUE: Deque[str] = collections.deque()
    _CACHE_MAXSIZE = 32
    _CACHE_LOCK = RLock()

    @classmethod
    def _add_new_cache_entry(cls, key, env_vars, binary, arguments, truncated):
        """Add a new entry to the command line cache."""
        if key in cls._CACHE:
            return

        cache_entry = SubprocessCmdLineCacheEntry()
        cache_entry.binary = binary
        cache_entry.arguments = arguments
        cache_entry.truncated = truncated
        cache_entry.env_vars = env_vars

        with cls._CACHE_LOCK:
            if len(cls._CACHE_DEQUE) >= cls._CACHE_MAXSIZE:
                # If the cache is full, remove the oldest entry
                last_cache_key = cls._CACHE_DEQUE[-1]
                del cls._CACHE[last_cache_key]
                cls._CACHE_DEQUE.pop()

            cls._CACHE[key] = cache_entry
            cls._CACHE_DEQUE.appendleft(key)

        return cache_entry

    @classmethod
    def _clear_cache(cls):
        """Clear all entries from the command line cache.

        Thread-safe method to completely clear the cache and deque.
        """
        with cls._CACHE_LOCK:
            cls._CACHE_DEQUE.clear()
            cls._CACHE.clear()

    TRUNCATE_LIMIT = 4 * 1024

    ENV_VARS_ALLOWLIST = {"LD_PRELOAD", "LD_LIBRARY_PATH", "PATH"}

    BINARIES_DENYLIST = {
        "md5",
    }

    SENSITIVE_WORDS_WILDCARDS = [
        "*password*",
        "*passwd*",
        "*mysql_pwd*",
        "*access_token*",
        "*auth_token*",
        "*api_key*",
        "*apikey*",
        "*secret*",
        "*credentials*",
        "stripetoken",
    ]
    _COMPILED_ENV_VAR_REGEXP = re.compile(r"\b[A-Z_]+=\w+")

    def __init__(self, shell_args: Union[str, List[str]], shell: bool = False) -> None:
        """
        For shell=True, the shell_args is parsed to extract environment variables,
        binary, and arguments. For shell=False, the first element is the binary
        and the rest are arguments.
        """
        cache_key = str(shell_args) + str(shell)
        self._cache_entry = SubprocessCmdLine._CACHE.get(cache_key)
        if self._cache_entry:
            self.env_vars = self._cache_entry.env_vars
            self.binary = self._cache_entry.binary
            self.arguments = self._cache_entry.arguments
            self.truncated = self._cache_entry.truncated
        else:
            self.env_vars = []
            self.binary = ""
            self.arguments = []
            self.truncated = False

            if isinstance(shell_args, str):
                tokens = shlex.split(shell_args)
            else:
                tokens = cast(List[str], shell_args)

            # Extract previous environment variables, scrubbing all the ones not
            # in ENV_VARS_ALLOWLIST
            if shell:
                self.scrub_env_vars(tokens)
            else:
                self.binary = tokens[0]
                self.arguments = tokens[1:]

            self.arguments = list(self.arguments) if isinstance(self.arguments, tuple) else self.arguments
            self.scrub_arguments()

            # Create a new cache entry to store the computed values except as_list
            # and as_string that are computed and stored lazily
            self._cache_entry = SubprocessCmdLine._add_new_cache_entry(
                cache_key, self.env_vars, self.binary, self.arguments, self.truncated
            )

    def scrub_env_vars(self, tokens):
        """Extract and scrub environment variables from shell command tokens.

        Args:
            tokens: List of command tokens to process

        Side effects:
            Updates self.env_vars, self.binary, and self.arguments
            Environment variables not in allowlist are scrubbed (value replaced with '?')
        """
        for idx, token in enumerate(tokens):
            if re.match(self._COMPILED_ENV_VAR_REGEXP, token):
                var, value = token.split("=")
                if var in self.ENV_VARS_ALLOWLIST:
                    self.env_vars.append(token)
                else:
                    # scrub the value
                    self.env_vars.append("%s=?" % var)
            else:
                # Next after vars are the binary and arguments
                try:
                    self.binary = tokens[idx]
                    self.arguments = tokens[idx + 1 :]
                except IndexError:
                    pass
                break

    def scrub_arguments(self):
        """Scrub sensitive information from command arguments.

        This method processes command arguments to remove or obscure sensitive
        information like passwords, API keys, tokens, etc. It handles both
        standalone sensitive arguments and argument-value pairs.

        Side effects:
            Updates self.arguments with scrubbed values (sensitive data replaced with '?')

        Scrubbing rules:
        1. If binary is in denylist, scrub all arguments
        2. For each argument matching sensitive patterns:
           - If it contains '=', scrub the entire argument
           - If it's followed by another option, scrub only this argument
           - If it's followed by a value, scrub the value instead
        """
        # if the binary is in the denylist, scrub all arguments
        if self.binary and self.binary.lower() in self.BINARIES_DENYLIST:
            self.arguments = ["?" for _ in self.arguments]
            return

        param_prefixes = ("-", "/")
        # Scrub case by case
        new_args = []
        deque_args = collections.deque(self.arguments)
        while deque_args:
            current = deque_args[0]
            for sensitive in self.SENSITIVE_WORDS_WILDCARDS + config.subprocess.sensitive_wildcards:
                if fnmatch(current, sensitive):
                    is_sensitive = True
                    break
            else:
                is_sensitive = False

            if not is_sensitive:
                new_args.append(current)
                deque_args.popleft()
                continue

            # sensitive
            if current[0] not in param_prefixes:
                # potentially not argument, scrub it anyway if it matches a sensitive word
                new_args.append("?")
                deque_args.popleft()
                continue

            # potential --argument
            if "=" in current:
                # contains "=" like in "--password=foo", scrub it just in case
                new_args.append("?")
                deque_args.popleft()
                continue

            try:
                if deque_args[1][0] in param_prefixes:
                    # Next is another option scrub only the current one
                    new_args.append("?")
                    deque_args.popleft()
                    continue
                else:
                    # Next is not an option but potentially a value, scrub it instead
                    new_args.extend([current, "?"])
                    deque_args.popleft()
                    deque_args.popleft()
                    continue
            except IndexError:
                # No next argument, scrub this one just in case since it's sensitive
                new_args.append("?")
                deque_args.popleft()

        self.arguments = new_args

    def truncate_string(self, str_: str) -> str:
        """Truncate a string if it exceeds the size limit.

        Args:
            str_: String to potentially truncate

        Returns:
            str: Original string if under limit, or truncated string with message

        Side effects:
            Sets self.truncated = True if truncation occurred
        """
        oversize = len(str_) - self.TRUNCATE_LIMIT

        if oversize <= 0:
            self.truncated = False
            return str_

        self.truncated = True

        msg = ' "4kB argument truncated by %d characters"' % oversize
        return str_[0 : -(oversize + len(msg))] + msg

    def _as_list_and_string(self) -> Tuple[List[str], str]:
        """Generate both list and string representations of the command.

        Returns:
            Tuple[List[str], str]: (command_as_list, command_as_string)

        Note:
            The string representation may be truncated if it exceeds size limits.
            The list representation is derived from the truncated string.
        """
        total_list = self.env_vars + [self.binary] + self.arguments
        truncated_str = self.truncate_string(join(total_list))
        truncated_list = shlex.split(truncated_str)
        return truncated_list, truncated_str

    def as_list(self):
        """Get the command as a list of strings.

        Returns:
            List[str]: Command represented as list of arguments

        Note:
            Result is cached for performance. Includes environment variables,
            binary, and arguments in that order.
        """
        if self._cache_entry and self._cache_entry.as_list is not None:
            return self._cache_entry.as_list

        list_res, str_res = self._as_list_and_string()
        if self._cache_entry:
            self._cache_entry.as_list = list_res
            self._cache_entry.as_string = str_res
        return list_res

    def as_string(self):
        """Get the command as a shell-quoted string.

        Returns:
            str: Command represented as a shell-quoted string

        Note:
            Result is cached for performance. String may be truncated if
            it exceeds the size limit.
        """
        if self._cache_entry and self._cache_entry.as_string is not None:
            return self._cache_entry.as_string

        list_res, str_res = self._as_list_and_string()
        if self._cache_entry:
            self._cache_entry.as_list = list_res
            self._cache_entry.as_string = str_res
        return str_res


def unpatch() -> None:
    """Remove instrumentation from subprocess and os functions.

    This function removes all patches applied by the patch() function,
    restoring the original behavior of subprocess and os functions.
    Also clears the command line cache.

    Note:
        Safe to call multiple times. Missing attributes are ignored.
    """
    import os  # nosec
    import subprocess  # nosec

    for obj, attr in [(os, "system"), (os, "_spawnvef"), (subprocess.Popen, "__init__"), (subprocess.Popen, "wait")]:
        try:
            trace_utils.unwrap(obj, attr)
        except AttributeError:
            pass

    SubprocessCmdLine._clear_cache()


@trace_utils.with_traced_module
def _traced_ossystem(module, pin, wrapped, instance, args, kwargs):
    """Traced wrapper for os.system function.

    Note:
        Only instruments when AAP or IAST is enabled and WAF bypass is not active.
        Creates spans with shell command details, exit codes, and component tags.
    """
    if not asm_config._bypass_instrumentation_for_waf and (asm_config._asm_enabled or asm_config._iast_enabled):
        try:
            if isinstance(args[0], str):
                for callback in _STR_CALLBACKS.values():
                    callback(args[0])
            shellcmd = SubprocessCmdLine(args[0], shell=True)  # nosec
        except Exception:  # noqa:E722
            log.debug("Could not trace subprocess execution for os.system", exc_info=True)
        with pin.tracer.trace(COMMANDS.SPAN_NAME, resource=shellcmd.binary, span_type=SpanTypes.SYSTEM) as span:
            span.set_tag_str(COMMANDS.SHELL, shellcmd.as_string())
            if shellcmd.truncated:
                span.set_tag_str(COMMANDS.TRUNCATED, "yes")
            span.set_tag_str(COMMANDS.COMPONENT, "os")
            ret = wrapped(*args, **kwargs)
            span.set_tag_str(COMMANDS.EXIT_CODE, str(ret))
            return ret
    else:
        return wrapped(*args, **kwargs)


@trace_utils.with_traced_module
def _traced_fork(module, pin, wrapped, instance, args, kwargs):
    """Traced wrapper for os.fork function.

    Note:
        Only instruments when AAP or IAST is enabled.
        Creates spans with fork operation details.
    """
    ret = wrapped(*args, **kwargs)
    if not (asm_config._asm_enabled or asm_config._iast_enabled):
        return ret
    try:
        with pin.tracer.trace(COMMANDS.SPAN_NAME, resource="fork", span_type=SpanTypes.SYSTEM) as span:
            span.set_tag(COMMANDS.EXEC, ["os.fork"])
            span.set_tag_str(COMMANDS.COMPONENT, "os")

    except Exception:  # noqa:E722
        log.debug("Could not trace subprocess execution for os.fork", exc_info=True)
    return ret


@trace_utils.with_traced_module
def _traced_osspawn(module, pin, wrapped, instance, args, kwargs):
    """Traced wrapper for os._spawnvef function (used by all os.spawn* variants).

    Note:
        Only instruments when AAP or IAST is enabled.
        Creates spans with spawn operation details and exit codes for P_WAIT mode.
    """
    if not (asm_config._asm_enabled or asm_config._iast_enabled):
        return wrapped(*args, **kwargs)
    try:
        mode, file, func_args, _, _ = args
        if isinstance(func_args, (list, tuple, str)):
            commands = [file] + list(func_args)
            for callback in _LST_CALLBACKS.values():
                callback(commands)
        shellcmd = SubprocessCmdLine(func_args, shell=False)
    except Exception:
        log.debug("Could not trace subprocess execution for os.spawn", exc_info=True)
        return wrapped(*args, **kwargs)

    with pin.tracer.trace(COMMANDS.SPAN_NAME, resource=shellcmd.binary, span_type=SpanTypes.SYSTEM) as span:
        span.set_tag(COMMANDS.EXEC, shellcmd.as_list())
        if shellcmd.truncated:
            span.set_tag_str(COMMANDS.TRUNCATED, "true")
        span.set_tag_str(COMMANDS.COMPONENT, "os")

        ret = wrapped(*args, **kwargs)
        if mode == os.P_WAIT:
            span.set_tag_str(COMMANDS.EXIT_CODE, str(ret))
        return ret


@trace_utils.with_traced_module
def _traced_subprocess_init(module, pin, wrapped, instance, args, kwargs):
    """Traced wrapper for subprocess.Popen.__init__ method.

    Note:
        Only instruments when AAP or IAST is enabled and WAF bypass is not active.
        Stores command details in context for later use by _traced_subprocess_wait.
        Creates a span that will be completed by the wait() method.
    """
    if not asm_config._bypass_instrumentation_for_waf and (asm_config._asm_enabled or asm_config._iast_enabled):
        try:
            cmd_args = args[0] if len(args) else kwargs["args"]
            if isinstance(cmd_args, (list, tuple, str)):
                if kwargs.get("shell", False):
                    for callback in _STR_CALLBACKS.values():
                        callback(cmd_args)
                else:
                    for callback in _LST_CALLBACKS.values():
                        callback(cmd_args)
            cmd_args_list = shlex.split(cmd_args) if isinstance(cmd_args, str) else cmd_args
            is_shell = kwargs.get("shell", False)
            shellcmd = SubprocessCmdLine(cmd_args_list, shell=is_shell)  # nosec

            with pin.tracer.trace(COMMANDS.SPAN_NAME, resource=shellcmd.binary, span_type=SpanTypes.SYSTEM):
                core.set_item(COMMANDS.CTX_SUBP_IS_SHELL, is_shell)

                if shellcmd.truncated:
                    core.set_item(COMMANDS.CTX_SUBP_TRUNCATED, "yes")

                if is_shell:
                    core.set_item(COMMANDS.CTX_SUBP_LINE, shellcmd.as_string())
                else:
                    core.set_item(COMMANDS.CTX_SUBP_LINE, shellcmd.as_list())
                core.set_item(COMMANDS.CTX_SUBP_BINARY, shellcmd.binary)
        except Exception:  # noqa:E722
            log.debug("Could not trace subprocess execution", exc_info=True)

    return wrapped(*args, **kwargs)


@trace_utils.with_traced_module
def _traced_subprocess_wait(module, pin, wrapped, instance, args, kwargs):
    """Traced wrapper for subprocess.Popen.wait method.

    Note:
        Only instruments when AAP or IAST is enabled and WAF bypass is not active.
        Retrieves command details stored by _traced_subprocess_init and completes
        the span with execution results and exit code.
    """
    ret = wrapped(*args, **kwargs)
    if not asm_config._bypass_instrumentation_for_waf and (asm_config._asm_enabled or asm_config._iast_enabled):
        try:
            binary = core.get_item("subprocess_popen_binary")

            with pin.tracer.trace(COMMANDS.SPAN_NAME, resource=binary, span_type=SpanTypes.SYSTEM) as span:
                if core.get_item(COMMANDS.CTX_SUBP_IS_SHELL):
                    span.set_tag_str(COMMANDS.SHELL, core.get_item(COMMANDS.CTX_SUBP_LINE))
                else:
                    span.set_tag(COMMANDS.EXEC, core.get_item(COMMANDS.CTX_SUBP_LINE))

                truncated = core.get_item(COMMANDS.CTX_SUBP_TRUNCATED)
                if truncated:
                    span.set_tag_str(COMMANDS.TRUNCATED, "yes")
                span.set_tag_str(COMMANDS.COMPONENT, "subprocess")
                span.set_tag_str(COMMANDS.EXIT_CODE, str(ret))
        except Exception:  # noqa:E722
            log.debug("Could not trace subprocess execution", exc_info=True)
    return ret

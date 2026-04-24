import collections
from dataclasses import dataclass
from fnmatch import fnmatch
import os
import re
import shlex
from shlex import join
from typing import Callable  # noqa:F401
from typing import Optional  # noqa:F401
from typing import Union  # noqa:F401
from typing import cast  # noqa:F401

from ddtrace._trace.pin import Pin
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.command import ProcessCommandEvent
from ddtrace.contrib._events.command import ShellCommandEvent
from ddtrace.contrib.internal.subprocess.constants import COMMANDS
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.runtime import get_runtime_propagation_envs
from ddtrace.internal.settings import env
from ddtrace.internal.settings._config import config
from ddtrace.internal.settings._telemetry import config as telemetry_config
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.threads import RLock
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.trace import tracer


log = get_logger(__name__)

config._add(
    "subprocess",
    dict(sensitive_wildcards=env.get("DD_SUBPROCESS_SENSITIVE_WILDCARDS", default="").split(",")),
)


def get_version() -> str:
    """Get the version string for the subprocess integration."""
    return ""


def _supported_versions() -> dict[str, str]:
    return {"subprocess": "*"}


def should_trace_subprocess():
    return not asm_config._bypass_instrumentation_for_waf and asm_config._asm_enabled


def patch() -> list[str]:
    """Patch subprocess and os functions to enable security monitoring and process lineage.

    Popen.__init__ is always patched for session lineage injection so that exec-based
    child processes receive lineage env vars whenever ddtrace is active, independent of
    whether ASM is enabled. Opt out via DD_TRACE_SUBPROCESS_ENABLED=false.

    Popen.wait is also always patched alongside Popen.__init__ to complete span
    lifecycle tracking, though _traced_subprocess_wait is a no-op without ASM.

    os.system, os.fork, and os._spawnvef are ASM-specific and only patched when ASM
    modules are loaded. AAP can be enabled dynamically via remote config, so
    already-patched functions are skipped.
    """
    import subprocess  # nosec

    patched: list[str] = []

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

    if not asm_config._load_modules:
        return patched

    import os  # nosec

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

    return patched


@dataclass(eq=False)
class SubprocessCmdLineCacheEntry:
    """Cache entry for storing parsed subprocess command line data.

    This class stores the parsed and processed command line arguments,
    environment variables, and metadata to avoid recomputing the same
    command line parsing multiple times.
    """

    binary: Optional[str] = None
    arguments: Optional[list] = None
    truncated: bool = False
    env_vars: Optional[list] = None
    as_list: Optional[list] = None
    as_string: Optional[str] = None


class SubprocessCmdLine:
    """Parser and scrubber for subprocess command lines.

    This class handles parsing, scrubbing, and caching of subprocess command lines
    for security monitoring. It supports both shell and non-shell commands,
    scrubs sensitive information, and provides caching for performance.
    """

    # This catches the computed values into a SubprocessCmdLineCacheEntry object
    _CACHE: dict[str, SubprocessCmdLineCacheEntry] = {}
    _CACHE_DEQUE: collections.deque[str] = collections.deque()
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

    def __init__(self, shell_args: Union[str, list[str]], shell: bool = False) -> None:
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
                tokens = cast(list[str], shell_args)

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
            tokens: list of command tokens to process

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

    def _as_list_and_string(self) -> tuple[list[str], str]:
        """Generate both list and string representations of the command.

        Returns:
            tuple[list[str], str]: (command_as_list, command_as_string)

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
            list[str]: Command represented as list of arguments

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

    # Remove Pin objects
    Pin().remove_from(os)
    Pin().remove_from(subprocess)

    # Unwrap all patched functions
    for obj, attr in [
        (os, "system"),
        (os, "fork"),
        (os, "_spawnvef"),
        (subprocess.Popen, "__init__"),
        (subprocess.Popen, "wait"),
    ]:
        try:
            trace_utils.unwrap(obj, attr)
        except AttributeError:
            pass

    SubprocessCmdLine._clear_cache()


@trace_utils.with_traced_module
def _traced_ossystem(module, pin, wrapped, instance, args, kwargs):
    """Traced wrapper for os.system function.

    Note:
        Only instruments when AAP is enabled and WAF bypass is not active.
        Creates spans with shell command details, exit codes, and component tags.
    """
    if should_trace_subprocess():
        try:
            if isinstance(args[0], str):
                core.dispatch_event(ShellCommandEvent(command=args[0]))
            shellcmd = SubprocessCmdLine(args[0], shell=True)  # nosec
        except Exception:  # noqa:E722
            log.debug("Could not trace subprocess execution for os.system", exc_info=True)
            return wrapped(*args, **kwargs)

        with tracer.trace(COMMANDS.SPAN_NAME, resource=shellcmd.binary, span_type=SpanTypes.SYSTEM) as span:
            span._set_attribute(COMMANDS.SHELL, shellcmd.as_string())
            if shellcmd.truncated:
                span._set_attribute(COMMANDS.TRUNCATED, "yes")
            span._set_attribute(COMMANDS.COMPONENT, "os")
            ret = wrapped(*args, **kwargs)
            span._set_attribute(COMMANDS.EXIT_CODE, str(ret))
            return ret
    else:
        return wrapped(*args, **kwargs)


@trace_utils.with_traced_module
def _traced_fork(module, pin, wrapped, instance, args, kwargs):
    """Traced wrapper for os.fork function.

    Note:
        Only instruments when AAP is enabled.
        Creates spans with fork operation details.
    """

    if not asm_config._asm_enabled:
        return wrapped(*args, **kwargs)

    with tracer.trace(COMMANDS.SPAN_NAME, resource="fork", span_type=SpanTypes.SYSTEM) as span:
        span.set_tag(COMMANDS.EXEC, ["os.fork"])
        span._set_attribute(COMMANDS.COMPONENT, "os")
        return wrapped(*args, **kwargs)


@trace_utils.with_traced_module
def _traced_osspawn(module, pin, wrapped, instance, args, kwargs):
    """Traced wrapper for os._spawnvef function (used by all os.spawn* variants).

    Note:
        Only instruments when AAP is enabled.
        Creates spans with spawn operation details and exit codes for P_WAIT mode.
    """
    if not asm_config._asm_enabled:
        return wrapped(*args, **kwargs)

    try:
        mode, file, func_args, _, _ = args
        if isinstance(func_args, (list, tuple)):
            commands = [str(file)] + [str(a) for a in func_args]
            core.dispatch_event(ProcessCommandEvent(command_args=commands))
        elif isinstance(func_args, str):
            core.dispatch_event(ProcessCommandEvent(command_args=[str(file), func_args]))
        shellcmd = SubprocessCmdLine(func_args, shell=False)
    except Exception:
        log.debug("Could not trace subprocess execution for os.spawn", exc_info=True)
        return wrapped(*args, **kwargs)

    with tracer.trace(COMMANDS.SPAN_NAME, resource=shellcmd.binary, span_type=SpanTypes.SYSTEM) as span:
        span.set_tag(COMMANDS.EXEC, shellcmd.as_list())
        if shellcmd.truncated:
            span._set_attribute(COMMANDS.TRUNCATED, "true")
        span._set_attribute(COMMANDS.COMPONENT, "os")

        ret = wrapped(*args, **kwargs)
        if mode == os.P_WAIT:
            span._set_attribute(COMMANDS.EXIT_CODE, str(ret))
        return ret


@trace_utils.with_traced_module
def _traced_subprocess_init(module, pin, wrapped, instance, args, kwargs):
    """Wrapper for ``subprocess.Popen.__init__``.

    When instrumentation telemetry is on, merges runtime propagation env vars into the
    child ``env`` (copying from ``os.environ`` if unset) so exec-based children keep
    lineage context.

    When ASM subprocess tracing is active, records the command for ``Popen.wait`` and
    emits a subprocess span around the real ``__init__``.
    """
    if telemetry_config.TELEMETRY_ENABLED:
        # Process tracking is only used in instrumentation telemetry. Skip if telemetry is disabled.
        # ``subprocess.Popen.__init__`` exposes ``env`` as the 11th argument (index 10) for
        # the Python versions we support.
        #
        # We build the child ``env`` from the parent (``os.environ``) merged with any
        # caller-supplied mapping. That is usually equivalent to ``env=None``, but it can
        # differ in edge cases and _may_ interact oddly with ``preexec_fn`` or other
        # POSIX-specific process setup.
        current_env = get_argument_value(args, kwargs, 10, "env", optional=True)
        if current_env is None:
            child_env = os.environ.copy()  # ast-grep-ignore: os-environ-fix-access
        else:
            child_env = current_env
        child_env.update(get_runtime_propagation_envs())
        args, kwargs = set_argument_value(args, kwargs, 10, "env", child_env, override_unset=True)

    if should_trace_subprocess():
        try:
            cmd_args = args[0] if len(args) else kwargs["args"]
            if isinstance(cmd_args, (list, tuple, str)):
                if kwargs.get("shell", False):
                    shell_command = cmd_args if isinstance(cmd_args, str) else " ".join(str(a) for a in cmd_args)
                    core.dispatch_event(ShellCommandEvent(command=shell_command))
                else:
                    process_args = cmd_args if isinstance(cmd_args, (list, str)) else list(cmd_args)
                    core.dispatch_event(ProcessCommandEvent(command_args=process_args))
            cmd_args_list = shlex.split(cmd_args) if isinstance(cmd_args, str) else cmd_args
            is_shell = kwargs.get("shell", False)
            shellcmd = SubprocessCmdLine(cmd_args_list, shell=is_shell)  # nosec
        except Exception:  # noqa:E722
            log.debug("Could not trace subprocess execution", exc_info=True)
            return wrapped(*args, **kwargs)

        with tracer.trace(COMMANDS.SPAN_NAME, resource=shellcmd.binary, span_type=SpanTypes.SYSTEM):
            core.set_item(COMMANDS.CTX_SUBP_IS_SHELL, is_shell)

            if shellcmd.truncated:
                core.set_item(COMMANDS.CTX_SUBP_TRUNCATED, "yes")

            if is_shell:
                core.set_item(COMMANDS.CTX_SUBP_LINE, shellcmd.as_string())
            else:
                core.set_item(COMMANDS.CTX_SUBP_LINE, shellcmd.as_list())
            core.set_item(COMMANDS.CTX_SUBP_BINARY, shellcmd.binary)
            return wrapped(*args, **kwargs)
    else:
        return wrapped(*args, **kwargs)


@trace_utils.with_traced_module
def _traced_subprocess_wait(module, pin, wrapped, instance, args, kwargs):
    """Traced wrapper for subprocess.Popen.wait method.

    Note:
        Only instruments when AAP is enabled and WAF bypass is not active.
        Retrieves command details stored by _traced_subprocess_init and completes
        the span with execution results and exit code.
    """
    if should_trace_subprocess():
        binary = core.find_item("subprocess_popen_binary")

        with tracer.trace(COMMANDS.SPAN_NAME, resource=binary, span_type=SpanTypes.SYSTEM) as span:
            if core.find_item(COMMANDS.CTX_SUBP_IS_SHELL):
                span._set_attribute(COMMANDS.SHELL, core.find_item(COMMANDS.CTX_SUBP_LINE))
            else:
                span.set_tag(COMMANDS.EXEC, core.find_item(COMMANDS.CTX_SUBP_LINE))

            truncated = core.find_item(COMMANDS.CTX_SUBP_TRUNCATED)
            if truncated:
                span._set_attribute(COMMANDS.TRUNCATED, "yes")
            span._set_attribute(COMMANDS.COMPONENT, "subprocess")
            ret = wrapped(*args, **kwargs)
            span._set_attribute(COMMANDS.EXIT_CODE, str(ret))
            return ret
    else:
        return wrapped(*args, **kwargs)

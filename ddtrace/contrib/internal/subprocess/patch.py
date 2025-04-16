import collections
from dataclasses import dataclass
from fnmatch import fnmatch
import os
import re
import shlex
from threading import RLock
from typing import Callable  # noqa:F401
from typing import Deque  # noqa:F401
from typing import Dict  # noqa:F401
from typing import List  # noqa:F401
from typing import Optional  # noqa:F401
from typing import Tuple  # noqa:F401
from typing import Union  # noqa:F401
from typing import cast  # noqa:F401

from ddtrace import config
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.subprocess.constants import COMMANDS
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.compat import shjoin
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config
from ddtrace.trace import Pin


log = get_logger(__name__)

config._add(
    "subprocess",
    dict(sensitive_wildcards=os.getenv("DD_SUBPROCESS_SENSITIVE_WILDCARDS", default="").split(",")),
)


def get_version() -> str:
    return ""


_STR_CALLBACKS: Dict[str, Callable[[str], None]] = {}
_LST_CALLBACKS: Dict[str, Callable[[Union[List[str], str]], None]] = {}


def add_str_callback(name: str, callback: Callable[[str], None]):
    _STR_CALLBACKS[name] = callback


def del_str_callback(name: str):
    _STR_CALLBACKS.pop(name, None)


def add_lst_callback(name: str, callback: Callable[[Union[List[str], str]], None]):
    _LST_CALLBACKS[name] = callback


def del_lst_callback(name: str):
    _LST_CALLBACKS.pop(name, None)


def patch() -> List[str]:
    if not asm_config._load_modules:
        return []
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
    binary: Optional[str] = None
    arguments: Optional[List] = None
    truncated: bool = False
    env_vars: Optional[List] = None
    as_list: Optional[List] = None
    as_string: Optional[str] = None


class SubprocessCmdLine:
    # This catches the computed values into a SubprocessCmdLineCacheEntry object
    _CACHE: Dict[str, SubprocessCmdLineCacheEntry] = {}
    _CACHE_DEQUE: Deque[str] = collections.deque()
    _CACHE_MAXSIZE = 32
    _CACHE_LOCK = RLock()

    @classmethod
    def _add_new_cache_entry(cls, key, env_vars, binary, arguments, truncated):
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
        # if the binary is in the denylist, scrub all arguments
        if self.binary.lower() in self.BINARIES_DENYLIST:
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
        oversize = len(str_) - self.TRUNCATE_LIMIT

        if oversize <= 0:
            self.truncated = False
            return str_

        self.truncated = True

        msg = ' "4kB argument truncated by %d characters"' % oversize
        return str_[0 : -(oversize + len(msg))] + msg

    def _as_list_and_string(self) -> Tuple[List[str], str]:
        total_list = self.env_vars + [self.binary] + self.arguments
        truncated_str = self.truncate_string(shjoin(total_list))
        truncated_list = shlex.split(truncated_str)
        return truncated_list, truncated_str

    def as_list(self):
        if self._cache_entry.as_list is not None:
            return self._cache_entry.as_list

        list_res, str_res = self._as_list_and_string()
        self._cache_entry.as_list = list_res
        self._cache_entry.as_string = str_res
        return list_res

    def as_string(self):
        if self._cache_entry.as_string is not None:
            return self._cache_entry.as_string

        list_res, str_res = self._as_list_and_string()
        self._cache_entry.as_list = list_res
        self._cache_entry.as_string = str_res
        return str_res


def unpatch() -> None:
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
    try:
        if asm_config._bypass_instrumentation_for_waf or not (asm_config._asm_enabled or asm_config._iast_enabled):
            return wrapped(*args, **kwargs)

        if isinstance(args[0], str):
            for callback in _STR_CALLBACKS.values():
                callback(args[0])
        shellcmd = SubprocessCmdLine(args[0], shell=True)  # nosec

        with pin.tracer.trace(COMMANDS.SPAN_NAME, resource=shellcmd.binary, span_type=SpanTypes.SYSTEM) as span:
            span.set_tag_str(COMMANDS.SHELL, shellcmd.as_string())
            if shellcmd.truncated:
                span.set_tag_str(COMMANDS.TRUNCATED, "yes")
            span.set_tag_str(COMMANDS.COMPONENT, "os")
            ret = wrapped(*args, **kwargs)
            span.set_tag_str(COMMANDS.EXIT_CODE, str(ret))
        return ret
    except Exception:  # noqa:E722
        log.debug("Could not trace subprocess execution for os.system", exc_info=True)
        return wrapped(*args, **kwargs)


@trace_utils.with_traced_module
def _traced_fork(module, pin, wrapped, instance, args, kwargs):
    if not (asm_config._asm_enabled or asm_config._iast_enabled):
        return wrapped(*args, **kwargs)
    try:
        with pin.tracer.trace(COMMANDS.SPAN_NAME, resource="fork", span_type=SpanTypes.SYSTEM) as span:
            span.set_tag(COMMANDS.EXEC, ["os.fork"])
            span.set_tag_str(COMMANDS.COMPONENT, "os")
            ret = wrapped(*args, **kwargs)
        return ret
    except Exception:  # noqa:E722
        log.debug("Could not trace subprocess execution for os.fork", exc_info=True)
        return wrapped(*args, **kwargs)


@trace_utils.with_traced_module
def _traced_osspawn(module, pin, wrapped, instance, args, kwargs):
    if not (asm_config._asm_enabled or asm_config._iast_enabled):
        return wrapped(*args, **kwargs)
    try:
        mode, file, func_args, _, _ = args
        if isinstance(func_args, (list, tuple, str)):
            commands = [file] + list(func_args)
            for callback in _LST_CALLBACKS.values():
                callback(commands)
        shellcmd = SubprocessCmdLine(func_args, shell=False)

        with pin.tracer.trace(COMMANDS.SPAN_NAME, resource=shellcmd.binary, span_type=SpanTypes.SYSTEM) as span:
            span.set_tag(COMMANDS.EXEC, shellcmd.as_list())
            if shellcmd.truncated:
                span.set_tag_str(COMMANDS.TRUNCATED, "true")
            span.set_tag_str(COMMANDS.COMPONENT, "os")

            if mode == os.P_WAIT:
                ret = wrapped(*args, **kwargs)
                span.set_tag_str(COMMANDS.EXIT_CODE, str(ret))
                return ret
    except Exception:  # noqa:E722
        log.debug("Could not trace subprocess execution for os.spawn", exc_info=True)

    return wrapped(*args, **kwargs)


@trace_utils.with_traced_module
def _traced_subprocess_init(module, pin, wrapped, instance, args, kwargs):
    try:
        if asm_config._bypass_instrumentation_for_waf or not (asm_config._asm_enabled or asm_config._iast_enabled):
            return wrapped(*args, **kwargs)

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
    try:
        if asm_config._bypass_instrumentation_for_waf or not (asm_config._asm_enabled or asm_config._iast_enabled):
            return wrapped(*args, **kwargs)

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
            ret = wrapped(*args, **kwargs)
            span.set_tag_str(COMMANDS.EXIT_CODE, str(ret))
            return ret
    except Exception:  # noqa:E722
        log.debug("Could not trace subprocess execution", exc_info=True)
        return wrapped(*args, **kwargs)

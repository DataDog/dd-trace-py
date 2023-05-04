import collections
import re
import shlex
import subprocess
from fnmatch import fnmatch
from typing import Union, Tuple

from ddtrace.internal import _context
from ddtrace import config

from ddtrace.ext import SpanTypes
from ddtrace.internal.logger import get_logger

from ddtrace import Pin

from ddtrace.contrib import trace_utils
import os

log = get_logger(__name__)


"""
JJJ TODO:
- add some catching (result for as_string() and as_list(), scrubbed stuff...) 
- make sure than _unpatch is called at the right time inside the tracer
- flask snapshot tests from views
- exception handlers so it never fails and always executes the command
- constants for the tag names
- rebase
"""



def _patch():
    # type: () -> None
    if not config._appsec_enabled:
        return

    import os
    Pin().onto(os)
    trace_utils.wrap(os, "system", traced_ossystem(os))
    # note: popen* uses subprocess, which we already wrap below

    # all os.spawn* variants eventually use this one:
    trace_utils.wrap(os, "_spawnvef", traced_osspawn(os))

    Pin().onto(subprocess)
    # We store the parameters on __init__ in the context and set the tags on wait
    # (where all the Popen objects eventually arrive, unless killed before it)
    trace_utils.wrap(subprocess, "Popen.__init__", traced_subprocess_init(subprocess))
    trace_utils.wrap(subprocess, "Popen.wait", traced_subprocess_wait(subprocess))


def _unpatch():
    # type: () -> None
    trace_utils.unwrap(os, "system")
    trace_utils.unwrap(os, "_spawnvef")
    trace_utils.unwrap(subprocess.Popen, "__init__")
    trace_utils.unwrap(subprocess.Popen, "wait")


_COMPILED_ENV_VAR_REGEXP = re.compile(r'\b[A-Z_]+=\w+')


class SubprocessCmdLine(object):
    TRUNCATE_LIMIT = 4*1024

    ENV_VARS_ALLOWLIST = {
        'LD_PRELOAD',
        'LD_LIBRARY_PATH',
        'PATH'
    }

    BINARIES_DENYLIST = {
        'md5',
    }

    SENSITIVE_WORDS_WILDCARDS = (
        "*password*", "*passwd*", "*mysql_pwd*",
        "*access_token*", "*auth_token*",
        "*api_key*", "*apikey*",
        "*secret*", "*credentials*", "stripetoken"
    )

    def __init__(self, shell_args, as_list=False, shell=False):
        # type: (Union[str, list], bool, bool) -> None
        self.env_vars = []
        self.binary = ''
        self.arguments = []
        self.truncated = False

        tokens = shell_args if as_list else shlex.split(shell_args)

        # Extract previous environment variables, removing all the ones not
        # in ENV_VARS_ALLOWLIST
        if shell:
            self.scrub_env_vars(tokens)
        else:
            self.binary = tokens[0]
            self.arguments = tokens[1:]

        self.arguments = list(self.arguments) if isinstance(self.arguments, tuple) else self.arguments
        self.scrub_arguments()


    def scrub_env_vars(self, tokens):
        for idx, token in enumerate(tokens):
            if re.match(_COMPILED_ENV_VAR_REGEXP, token):
                var, value = token.split('=')
                if var in self.ENV_VARS_ALLOWLIST:
                    self.env_vars.append(token)
                else:
                    # scrub the value
                    self.env_vars.append("%s=?" % var)
            else:
                # Next after vars are the binary and arguments
                try:
                    self.binary = tokens[idx]
                    self.arguments = tokens[idx + 1:]
                except IndexError:
                    pass
                break

    def scrub_arguments(self):
        # if the binary is in the denylist, scrub all arguments
        if self.binary.lower() in self.BINARIES_DENYLIST:
            self.arguments = ['?' for _ in self.arguments]
            return

        param_prefixes = ('-', '/')
        # Scrub case by case
        new_args = []
        deque_args = collections.deque(self.arguments)
        while deque_args:
            current = deque_args[0]
            for sensitive in self.SENSITIVE_WORDS_WILDCARDS:
                if fnmatch(current, sensitive):
                    is_sensitive = True
                    break
            else:
                is_sensitive=False

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


    def truncate_string(self, str_):
        # type: (str) -> str
        # spaced_added is to account for spaces that would not occupy
        # space on a list result
        oversize = len(str_) - self.TRUNCATE_LIMIT

        if oversize <= 0:
            self.truncated = False
            return str_

        self.truncated = True

        msg = ' "4kB argument truncated by %d characters"' % oversize
        return str_[0:-(oversize+len(msg))] + msg


    def as_list_and_string(self):
        # type: () -> Tuple[list[str], str]

        total_list = self.env_vars + [self.binary] + self.arguments
        truncated_str = self.truncate_string(shlex.join(total_list))
        truncated_list = shlex.split(truncated_str)
        return truncated_list, truncated_str


    def as_list(self):
        return self.as_list_and_string()[0]


    def as_string(self):
        return self.as_list_and_string()[1]


def scrub_arg(_arg):
    # type: (str) -> str
    # JJJ better scrubbing
    return _arg


@trace_utils.with_traced_module
def traced_ossystem(module, pin, wrapped, instance, args, kwargs):
    with pin.tracer.trace("os.system", span_type=SpanTypes.SYSTEM) as span:
        shellcmd = SubprocessCmdLine(args[0], shell=True)
        span.set_tag_str("name", "command_execution")
        span.set_tag_str("cmd.shell", shellcmd.as_string())
        if shellcmd.truncated:
            span.set_tag_str("cmd.truncated", "yes")
        span.set_tag_str("component", "os")
        span.set_tag_str("resource", shellcmd.binary)
        ret = wrapped(*args, **kwargs)
        span.set_tag_str("cmd.exit_code", str(ret))
    return ret


@trace_utils.with_traced_module
def traced_osspawn(module, pin, wrapped, instance, args, kwargs):
    with pin.tracer.trace("os.spawn", span_type=SpanTypes.SYSTEM) as span:
        span.set_tag_str("name", "command_execution")
        mode, file, func_args, _, _ = args

        shellcmd = SubprocessCmdLine(func_args, True, shell=False)
        span.set_tag("cmd.exec", shellcmd.as_list())
        if shellcmd.truncated:
            span.set_tag_str("cmd.truncated", "true")
        span.set_tag_str("component", "os")
        span.set_tag_str("resource", shellcmd.binary)

        if mode == os.P_WAIT:
            ret = wrapped(*args, **kwargs)
            span.set_tag_str("cmd.exit_code", str(ret))
            return ret

        return wrapped(*args, **kwargs)


@trace_utils.with_traced_module
def traced_subprocess_init(module, pin, wrapped, instance, args, kwargs):
    with pin.tracer.trace("subprocess.Popen.init", span_type=SpanTypes.SYSTEM) as span:
        cmd_args = args[0] if len(args) else kwargs["args"]
        cmd_args_list = shlex.split(cmd_args) if isinstance(cmd_args, str) else cmd_args
        is_shell = kwargs.get("shell", False)
        _context.set_item("subprocess_popen_is_shell", is_shell, span=span)

        shellcmd = SubprocessCmdLine(cmd_args_list, True, shell=is_shell)
        if shellcmd.truncated:
            _context.set_item("subprocess_popen_truncated", "yes", span=span)

        if is_shell:
            _context.set_item("subprocess_popen_line", shellcmd.as_string(), span=span)
        else:
            _context.set_item("subprocess_popen_line", shellcmd.as_list(), span=span)

        _context.set_item("subprocess_popen_binary", shellcmd.binary, span=span)

        wrapped(*args, **kwargs)


@trace_utils.with_traced_module
def traced_subprocess_wait(module, pin, wrapped, instance, args, kwargs):
    with pin.tracer.trace("subprocess.Popen.wait", span_type=SpanTypes.SYSTEM) as span:
        span.set_tag_str("name", "command_execution")

        if _context.get_item("subprocess_popen_is_shell", span=span):
            span.set_tag_str("cmd.shell", _context.get_item("subprocess_popen_line", span=span))
        else:
            span.set_tag("cmd.exec", _context.get_item("subprocess_popen_line", span=span))

        truncated = _context.get_item("subprocess_popen_truncated", span=span)
        if truncated:
            span.set_tag_str("cmd.truncated", "yes")
        span.set_tag_str("component", "subprocess")
        span.set_tag_str("resource", _context.get_item("subprocess_popen_binary", span=span))
        ret = wrapped(*args, **kwargs)
        span.set_tag_str("cmd.exit_code", str(ret))
        return ret


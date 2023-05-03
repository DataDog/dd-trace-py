import re
import shlex
import subprocess
from typing import Union

from ddtrace.internal import _context
from ddtrace import config

from ddtrace.ext import SpanTypes
from ddtrace.internal.logger import get_logger
from ddtrace.internal.compat import PY2

from ddtrace import Pin

from ddtrace.contrib import trace_utils
import os

log = get_logger(__name__)


"""
JJJ TODO:
- Truncation
- Param scrubbing
- cmd denylist
- unpatch
- flask snapshot tests from views
- exception handlers so it never fails and always executes the command
- constants for the tag names
"""



def _patch():
    # type: () -> None
    if not config._appsec_enabled:
        return

    import os
    Pin().onto(os)
    trace_utils.wrap(os, "system", traced_ossystem(os))
    # note: popen* uses subprocess, which we alredy wrap below

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
    # trace_utils.unwrap(os, "popen")
    trace_utils.unwrap(os, "_spawnvef")
    trace_utils.unwrap(subprocess.Popen, "__init__")
    trace_utils.unwrap(subprocess.Popen, "wait")

    if PY2:
        trace_utils.unwrap(os, "popen2")
        trace_utils.unwrap(os, "popen3")
        trace_utils.unwrap(os, "popen4")


_COMPILED_ENV_VAR_REGEXP = re.compile(r'\b[A-Z_]+=\w+')


class ShellCmdLine(object):
    ENV_VARS_ALLOWLIST = {
        'LD_PRELOAD',
        'LD_LIBRARY_PATH',
        'PATH'
    }

    def __init__(self, shell_args, as_list=False):
        # type: (Union[str, list], bool) -> None
        self.env_vars = []
        self.binary = ''
        self.arguments = []
        self.truncated = "false"

        tokens = shell_args if as_list else shlex.split(shell_args)

        # Extract previous environment variables, removing all the ones not
        # in ENV_VARS_ALLOWLIST
        for idx, token in enumerate(tokens):
            if re.match(_COMPILED_ENV_VAR_REGEXP, token):
                var, value = token.split('=')
                if var in self.ENV_VARS_ALLOWLIST:
                    self.env_vars.append(token)
            else:
                # Next after vars are the binary and arguments
                try:
                    self.binary = tokens[idx]
                    self.arguments = tokens[idx+1:]
                except IndexError:
                    pass
                break

    def as_list(self):
        return self.env_vars + [self.binary] + self.arguments

    def as_string(self):
        return shlex.join(self.as_list())


def scrub_arg(_arg):
    # type: (str) -> str
    # JJJ better scrubbing
    return _arg


def _scrub_params_maybe_truncate(_params):
    # type: (list[str]) -> tuple[str, list[str]]

    # JJJ do truncation if needed!
    truncated = "false"

    return truncated, [scrub_arg(a) for a in _params]


def parse_exec_args(_args_list):
    # type: (list[str]) -> tuple[str, list[str]]

    truncated, params = _scrub_params_maybe_truncate(_args_list)
    return truncated, params


@trace_utils.with_traced_module
def traced_ossystem(module, pin, wrapped, instance, args, kwargs):
    with pin.tracer.trace("os.system", span_type=SpanTypes.SYSTEM) as span:
        shellcmd = ShellCmdLine(args[0])
        span.set_tag_str("name", "command_execution")
        span.set_tag_str("cmd.shell", shellcmd.as_string())
        span.set_tag_str("cmd.truncated", shellcmd.truncated)
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

        truncated, exec_tokens = parse_exec_args(func_args)
        span.set_tag_str("cmd.exec", " ".join(exec_tokens))
        span.set_tag_str("cmd.truncated", truncated)
        span.set_tag_str("component", "os")
        span.set_tag_str("resource", file)

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

        if is_shell:
            shellcmd = ShellCmdLine(cmd_args_list, True)
            truncated = shellcmd.truncated
            binary = shellcmd.binary
            tokens = shellcmd.as_list()
        else:
            truncated, tokens = parse_exec_args(cmd_args_list)
            binary = tokens[0]
        # JJJ add binary
        _context.set_item("subprocess_popen_truncated", truncated)
        _context.set_item("subprocess_popen_line", " ".join(tokens))
        _context.set_item("subprocess_popen_binary", binary)

        wrapped(*args, **kwargs)


@trace_utils.with_traced_module
def traced_subprocess_wait(module, pin, wrapped, instance, args, kwargs):
    with pin.tracer.trace("subprocess.Popen.wait", span_type=SpanTypes.SYSTEM) as span:
        span.set_tag_str("name", "command_execution")

        sh_tag = "cmd.shell" if _context.get_item("subprocess_popen_is_shell") else "cmd.exec"
        span.set_tag_str(sh_tag, _context.get_item("subprocess_popen_line"))
        span.set_tag_str("cmd.truncated", _context.get_item("subprocess_popen_truncated"))
        span.set_tag_str("component", "subprocess")
        span.set_tag_str("resource", _context.get_item("subprocess_popen_binary"))
        ret = wrapped(*args, **kwargs)
        span.set_tag_str("cmd.exit_code", str(ret))
        return ret


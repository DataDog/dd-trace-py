"""PreToolUse hook: deny Bash commands that run pytest directly.

AGENTS.md requires test runs to go through scripts/run-tests, which selects the
suitespec environment, pins its dependencies, and starts the services a suite
needs. This hook enforces that rule for commands such as "pytest tests/x",
"python -m pytest", ".venv/bin/pytest", "uv run pytest", "env -u X pytest",
"cd tests && pytest", and "bash -c 'pytest ...'". Commands that only mention
pytest (grep, pip install, git commit messages) and scripts/run-tests itself
are allowed. Commands that fail to parse are allowed: this is a guardrail for the
documented rule, not a sandbox.
"""

import json
import os
import re
import shlex
import sys


REASON = (
    "AGENTS.md: never run pytest directly. Discover environments with "
    "`scripts/run-tests --list <changed-files>` and run the returned hash with "
    "`scripts/run-tests` (see the run-tests skill). If scripts/run-tests cannot run "
    "here (for example Docker is unavailable), stop and report that instead of "
    "falling back to pytest."
)

PYTEST_PROGRAMS = {"pytest", "py.test"}
PYTHON_PROGRAM = re.compile(r"^python(\d+(\.\d+)?)?$")
SHELLS = {"sh", "bash", "zsh"}
SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "|&", ";;"}
ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Wrappers that run their remaining arguments as a command.
PLAIN_WRAPPERS = {"command", "exec", "time", "nohup"}
# Project runners: "<runner> run [options] <command>".
RUNNERS = {"uv", "hatch", "poetry", "pdm", "pipenv"}
# uv run options that consume the next token as their value.
RUNNER_VALUE_OPTIONS = {
    "--with",
    "-w",
    "--with-editable",
    "--with-requirements",
    "--python",
    "-p",
    "--project",
    "--directory",
    "--package",
    "--extra",
    "--group",
    "--env-file",
    "--index",
    "--default-index",
    "--index-url",
}


def _split_commands(command):
    """Split a shell command line into simple commands (lists of words)."""
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    commands = [[]]
    for token in lexer:
        if token in SEPARATORS:
            commands.append([])
        else:
            commands[-1].append(token)
    return [words for words in commands if words]


def _unwrap(words):
    """Drop env assignments and wrapper programs in front of the real program."""
    while words:
        program = os.path.basename(words[0])
        if ENV_ASSIGNMENT.match(words[0]):
            words = words[1:]
        elif program == "env":
            words = words[1:]
            while words and (words[0].startswith("-") or ENV_ASSIGNMENT.match(words[0])):
                takes_value = words[0] in ("-u", "--unset", "-C", "--chdir")
                words = words[2:] if takes_value else words[1:]
        elif program in PLAIN_WRAPPERS:
            words = words[1:]
        elif program == "timeout":
            words = words[1:]
            while words and words[0].startswith("-"):
                words = words[1:]
            words = words[1:]  # the duration
        elif program == "nice":
            words = words[1:]
            if words and words[0] == "-n":
                words = words[2:]
        elif program in RUNNERS and len(words) > 1 and words[1] == "run":
            words = words[2:]
            while words and words[0].startswith("-"):
                words = words[2:] if words[0] in RUNNER_VALUE_OPTIONS else words[1:]
        else:
            return words
    return words


def _runs_pytest(words):
    words = _unwrap(words)
    if not words:
        return False
    program = os.path.basename(words[0])
    if program in PYTEST_PROGRAMS:
        return True
    args = words[1:]
    if PYTHON_PROGRAM.match(program):
        return "-mpytest" in args or any(a == "-m" and b == "pytest" for a, b in zip(args, args[1:]))
    if program in SHELLS and "-c" in args:
        index = args.index("-c") + 1
        return index < len(args) and command_runs_pytest(args[index])
    return False


def command_runs_pytest(command):
    try:
        commands = _split_commands(command)
    except ValueError:
        return False
    return any(_runs_pytest(words) for words in commands)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name") != "Bash":
        return
    command = (data.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not command_runs_pytest(command):
        return
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": REASON,
                }
            }
        )
    )


if __name__ == "__main__":
    main()

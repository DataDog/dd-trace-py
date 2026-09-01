"""Auto-instrumentation must reach evaluate() through evaluate_auto (APPSEC-69866).

evaluate_auto binds source=auto and the DD_AI_GUARD_BLOCK policy, so a listener cannot forget
or mismatch a call-path tag. What it cannot prevent is a listener calling client.evaluate()
directly and going out untagged, which is what the source scan below guards. Checked statically
because it is the only way to cover litellm and strands from this suite, which does not install
those packages. Spec: https://datadoghq.atlassian.net/wiki/spaces/AIGuard/pages/6600426215
"""

import ast
import pathlib

import pytest

from ddtrace.aiguard._common import evaluate_auto
from ddtrace.aiguard._constants import AI_GUARD
from ddtrace.internal.settings.aiguard import aiguard_config


INTEGRATIONS_DIR = pathlib.Path(__file__).parents[3] / "ddtrace" / "aiguard" / "integrations"

# litellm resolves block per request from its own dynamic guardrail params, so it tags its
# evaluate() call explicitly instead of going through evaluate_auto.
ALLOWED_DIRECT_CALLERS = {"litellm.py"}


class _FakeClient:
    def __init__(self):
        self.calls = []

    def evaluate(self, messages, options=None, source=None, integration=None):
        self.calls.append({"messages": messages, "options": options, "source": source, "integration": integration})


def test_evaluate_auto_reports_the_package_as_the_call_path():
    client = _FakeClient()
    messages = [{"role": "user", "content": "hi"}]

    evaluate_auto(client, messages, AI_GUARD.INTEGRATION_OPENAI)

    assert client.calls == [
        {
            "messages": messages,
            "options": {"block": aiguard_config._ai_guard_block},
            "source": AI_GUARD.SOURCE_AUTO,
            "integration": AI_GUARD.INTEGRATION_OPENAI,
        }
    ]


@pytest.mark.parametrize("path", sorted(INTEGRATIONS_DIR.glob("*.py")), ids=lambda path: path.name)
def test_listeners_do_not_call_evaluate_directly(path):
    if path.name in ALLOWED_DIRECT_CALLERS:
        return

    tree = ast.parse(path.read_text())
    direct = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "evaluate"
    ]
    assert not direct, f"{path.name}:{direct} calls evaluate() directly; use evaluate_auto so the call path is tagged"

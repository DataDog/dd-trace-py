"""Every auto-instrumentation evaluate() call site must tag its call path (APPSEC-69866).

Checked statically rather than by exercising each provider: the assertion is uniform across
providers and a source scan is the only way to cover litellm and strands from this suite, which
does not install those packages. It also fails loudly when a new call site (or a whole new
integration module) forgets the tags -- the regression the telemetry spec is guarding against.
Spec: https://datadoghq.atlassian.net/wiki/spaces/AIGuard/pages/6600426215
"""

import ast
import pathlib

import pytest

from ddtrace.aiguard._constants import AI_GUARD


INTEGRATIONS_DIR = pathlib.Path(__file__).parents[3] / "ddtrace" / "aiguard" / "integrations"

# Module basename -> the AI_GUARD.INTEGRATION_* constant its evaluate() calls must report.
EXPECTED_INTEGRATION = {
    "_anthropic.py": "INTEGRATION_ANTHROPIC",
    "_langchain.py": "INTEGRATION_LANGCHAIN",
    "_openai_chat.py": "INTEGRATION_OPENAI",
    "_openai_responses.py": "INTEGRATION_OPENAI",
    "litellm.py": "INTEGRATION_LITELLM",
    "strands.py": "INTEGRATION_STRANDS",
}


def _is_evaluate_attribute(node):
    return isinstance(node, ast.Attribute) and node.attr == "evaluate"


def _evaluate_calls(tree):
    """Yield the Call nodes carrying the evaluate() keyword arguments.

    Direct calls (client.evaluate(...)) carry them themselves; litellm dispatches through
    asyncio.to_thread(self._client.evaluate, ...), where the outer call carries them instead.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _is_evaluate_attribute(node.func) or any(_is_evaluate_attribute(arg) for arg in node.args):
            yield node


def _constant_name(keyword):
    """The AI_GUARD.<NAME> referenced by a keyword argument, or None if it is not one."""
    value = keyword.value
    if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name) and value.value.id == "AI_GUARD":
        return value.attr
    return None


@pytest.mark.parametrize("path", sorted(INTEGRATIONS_DIR.glob("*.py")), ids=lambda path: path.name)
def test_auto_instrumentation_evaluate_calls_tag_the_call_path(path):
    tree = ast.parse(path.read_text())
    calls = list(_evaluate_calls(tree))
    if not calls:
        return

    assert path.name in EXPECTED_INTEGRATION, (
        f"{path.name} calls evaluate() but has no expected integration tag; add it to "
        "EXPECTED_INTEGRATION and to AI_GUARD.INTEGRATION_*"
    )
    expected_integration = EXPECTED_INTEGRATION[path.name]

    for call in calls:
        keywords = {keyword.arg: keyword for keyword in call.keywords if keyword.arg}
        where = f"{path.name}:{call.lineno}"
        assert "source" in keywords and "integration" in keywords, f"{where}: evaluate() call is untagged"
        assert _constant_name(keywords["source"]) == "SOURCE_AUTO", f"{where}: source must be AI_GUARD.SOURCE_AUTO"
        assert _constant_name(keywords["integration"]) == expected_integration, (
            f"{where}: integration must be AI_GUARD.{expected_integration}"
        )


def test_expected_integrations_are_declared_constants():
    for name in set(EXPECTED_INTEGRATION.values()):
        assert name in AI_GUARD

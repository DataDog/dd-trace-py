import os

from ddtrace.appsec._iast.constants import VULN_STACKTRACE_LEAK
from ddtrace.appsec._iast.taint_sinks.stacktrace_leak import asm_check_stacktrace_leak
from tests.appsec.iast.taint_sinks.conftest import _get_span_report


def _load_html_django_stacktrace():
    return open(os.path.join(os.path.dirname(__file__),
                                            "../fixtures/django_debug_page.html")).read()


_plain_text_stacktrace = r"""
Environment:


Request Method: GET
Request URL: http://localhost:8000/

Django Version: 5.1.5
Python Version: 3.12.5
Installed Applications:
[]
Installed Middleware:
[]

Traceback (most recent call last):
  File "/usr/local/lib/python3.9/site-packages/some_module.py", line 42, in process_data
    result = complex_calculation(data)
  File "/usr/local/lib/python3.9/site-packages/another_module.py", line 158, in complex_calculation
    intermediate = perform_subtask(data_slice)
  File "/usr/local/lib/python3.9/site-packages/subtask_module.py", line 27, in perform_subtask
    processed = handle_special_case(data_slice)
  File "/usr/local/lib/python3.9/site-packages/special_cases.py", line 84, in handle_special_case
    return apply_algorithm(data_slice, params)
  File "/usr/local/lib/python3.9/site-packages/algorithm_module.py", line 112, in apply_algorithm
    step_result = execute_step(data, params)
  File "/usr/local/lib/python3.9/site-packages/step_execution.py", line 55, in execute_step
    temp = pre_process(data)
  File "/usr/local/lib/python3.9/site-packages/pre_processing.py", line 33, in pre_process
    validated_data = validate_input(data)
  File "/usr/local/lib/python3.9/site-packages/validation.py", line 66, in validate_input
    check_constraints(data)
  File "/usr/local/lib/python3.9/site-packages/constraints.py", line 19, in check_constraints
    raise ValueError("Constraint violation at step 9")
ValueError: Constraint violation at step 9

Lorem Ipsum Foobar
"""


def test_asm_check_stacktrace_leak_html(iast_context_defaults):
    asm_check_stacktrace_leak(_load_html_django_stacktrace())
    span_report = _get_span_report()
    vulnerabilities = list(span_report.vulnerabilities)
    vulnerabilities_types = [vuln.type for vuln in vulnerabilities]
    assert len(vulnerabilities) == 1
    assert VULN_STACKTRACE_LEAK in vulnerabilities_types
    assert (
        vulnerabilities[0].evidence.value
        == "Module: home.foobaruser.sources.minimal-django-example.app\nException: IndexError"
    )


def test_asm_check_stacktrace_leak_text(iast_context_defaults):
    asm_check_stacktrace_leak(_plain_text_stacktrace)
    span_report = _get_span_report()
    vulnerabilities = list(span_report.vulnerabilities)
    vulnerabilities_types = [vuln.type for vuln in vulnerabilities]
    assert len(vulnerabilities) == 1
    assert VULN_STACKTRACE_LEAK in vulnerabilities_types
    assert (
        vulnerabilities[0].evidence.value
        == "Module: usr.local.lib.python3.9.site-packages.constraints\nException: ValueError"
    )

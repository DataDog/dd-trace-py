"""PEP 750 template strings (t"...") in a wrapped body — Python 3.14+.

Collected only on 3.14+ (see conftest.py) — the exact construct the IAST suite's
``collect_ignore`` guard exists for.

Finding: ``WrappingContext.wrap()`` round-trips the wrapped function's own bytecode
through the ``bytecode`` library to inject enter/exit. As of bytecode 0.18.1 (the
latest, matching the >=0.17,<1 pin) that library cannot re-encode t-string opcodes,
so it raises AssertionError for ANY function whose body contains a t-string. The
trampoline-based ``internal_wrap`` is immune (it never re-encodes the body).
"""

from tests.wrapping.mechanisms import xfail_mechanism


@xfail_mechanism(
    "wrapping_context",
    reason="bytecode lib can't re-encode PEP 750 t-string opcodes; WrappingContext.wrap() crashes",
)
def test_tstring_in_function_body(mech):
    def f(x):
        template = t"value={x}"
        return (template.strings, template.values)

    g = mech.wrap_function(f)
    assert g(7) == (("value=", ""), (7,))


@xfail_mechanism(
    "wrapping_context",
    reason="bytecode lib can't re-encode PEP 750 t-string opcodes; WrappingContext.wrap() crashes",
)
def test_tstring_in_generator_body(mech):
    def g(x):
        template = t"value={x}"
        yield (template.strings, template.values)

    assert list(mech.wrap_function(g)(7)) == [(("value=", ""), (7,))]

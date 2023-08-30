# Tests are copied from cpython/Lib/test/test_context.py
# License: PSFL
# Copyright: 2018 Python Software Foundation


import concurrent.futures
import functools
import random
import time
import unittest

import pytest

from ddtrace.vendor import contextvars


def isolated_context(func):
    """Needed to make reftracking test mode work."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        ctx = contextvars.Context()
        return ctx.run(func, *args, **kwargs)

    return wrapper


class ContextTest(unittest.TestCase):
    def test_context_var_new_1(self):
        with self.assertRaises(TypeError):
            contextvars.ContextVar()

        with pytest.raises(TypeError) as e:
            contextvars.ContextVar(1)
        assert "must be a str" in str(e.value)

        c = contextvars.ContextVar("a")
        self.assertNotEqual(hash(c), hash("a"))

    @isolated_context
    def test_context_var_repr_1(self):
        c = contextvars.ContextVar("a")
        self.assertIn("a", repr(c))

        c = contextvars.ContextVar("a", default=123)
        self.assertIn("123", repr(c))

        lst = []
        c = contextvars.ContextVar("a", default=lst)
        lst.append(c)
        self.assertIn("...", repr(c))
        self.assertIn("...", repr(lst))

        t = c.set(1)
        self.assertIn(repr(c), repr(t))
        self.assertNotIn(" used ", repr(t))
        c.reset(t)
        self.assertIn(" used ", repr(t))

    def test_context_new_1(self):
        with self.assertRaises(TypeError):
            contextvars.Context(1)
        with self.assertRaises(TypeError):
            contextvars.Context(1, a=1)
        with self.assertRaises(TypeError):
            contextvars.Context(a=1)
        contextvars.Context(**{})

    def test_context_typerrors_1(self):
        ctx = contextvars.Context()

        with pytest.raises(TypeError) as e:
            ctx[1]
        assert "ContextVar key was expected" in str(e.value)

        with pytest.raises(TypeError):
            assert 1 in ctx
        assert "ContextVar key was expected" in str(e.value)

        with pytest.raises(TypeError) as e:
            ctx.get(1)
        assert "ContextVar key was expected" in str(e.value)

    def test_context_get_context_1(self):
        ctx = contextvars.copy_context()
        self.assertIsInstance(ctx, contextvars.Context)

    def test_context_run_1(self):
        ctx = contextvars.Context()

        with pytest.raises(TypeError):
            ctx.run()

    def test_context_run_2(self):
        ctx = contextvars.Context()

        def func(*args, **kwargs):
            kwargs["spam"] = "foo"
            args += ("bar",)
            return args, kwargs

        for f in (func, functools.partial(func)):
            # partial doesn't support FASTCALL

            self.assertEqual(ctx.run(f), (("bar",), {"spam": "foo"}))
            self.assertEqual(ctx.run(f, 1), ((1, "bar"), {"spam": "foo"}))

            self.assertEqual(ctx.run(f, a=2), (("bar",), {"a": 2, "spam": "foo"}))

            self.assertEqual(ctx.run(f, 11, a=2), ((11, "bar"), {"a": 2, "spam": "foo"}))

            a = {}
            self.assertEqual(ctx.run(f, 11, **a), ((11, "bar"), {"spam": "foo"}))
            self.assertEqual(a, {})

    def test_context_run_3(self):
        ctx = contextvars.Context()

        def func(*args, **kwargs):
            1 / 0

        with self.assertRaises(ZeroDivisionError):
            ctx.run(func)
        with self.assertRaises(ZeroDivisionError):
            ctx.run(func, 1, 2)
        with self.assertRaises(ZeroDivisionError):
            ctx.run(func, 1, 2, a=123)

    @isolated_context
    def test_context_run_4(self):
        ctx1 = contextvars.Context()
        ctx2 = contextvars.Context()
        var = contextvars.ContextVar("var")

        def func2():
            self.assertIsNone(var.get(None))

        def func1():
            self.assertIsNone(var.get(None))
            var.set("spam")
            ctx2.run(func2)
            self.assertEqual(var.get(None), "spam")

            cur = contextvars.copy_context()
            self.assertEqual(len(cur), 1)
            self.assertEqual(cur[var], "spam")
            return cur

        returned_ctx = ctx1.run(func1)
        self.assertEqual(ctx1, returned_ctx)
        self.assertEqual(returned_ctx[var], "spam")
        self.assertIn(var, returned_ctx)

    def test_context_run_5(self):
        ctx = contextvars.Context()
        var = contextvars.ContextVar("var")

        def func():
            self.assertIsNone(var.get(None))
            var.set("spam")
            1 / 0

        with self.assertRaises(ZeroDivisionError):
            ctx.run(func)

        self.assertIsNone(var.get(None))

    def test_context_run_6(self):
        ctx = contextvars.Context()
        c = contextvars.ContextVar("a", default=0)

        def fun():
            self.assertEqual(c.get(), 0)
            self.assertIsNone(ctx.get(c))

            c.set(42)
            self.assertEqual(c.get(), 42)
            self.assertEqual(ctx.get(c), 42)

        ctx.run(fun)

    def test_context_run_7(self):
        ctx = contextvars.Context()

        def fun():
            with pytest.raises(RuntimeError) as e:
                ctx.run(fun)
            assert "is already entered" in str(e.value)

        ctx.run(fun)

    @isolated_context
    def test_context_getset_1(self):
        c = contextvars.ContextVar("c")
        with self.assertRaises(LookupError):
            c.get()

        self.assertIsNone(c.get(None))

        t0 = c.set(42)
        self.assertEqual(c.get(), 42)
        self.assertEqual(c.get(None), 42)
        self.assertIs(t0.old_value, t0.MISSING)
        self.assertIs(t0.old_value, contextvars.Token.MISSING)
        self.assertIs(t0.var, c)

        t = c.set("spam")
        self.assertEqual(c.get(), "spam")
        self.assertEqual(c.get(None), "spam")
        self.assertEqual(t.old_value, 42)
        c.reset(t)

        self.assertEqual(c.get(), 42)
        self.assertEqual(c.get(None), 42)

        c.set("spam2")
        with pytest.raises(RuntimeError) as e:
            c.reset(t)
        assert "has already been used" in str(e.value)
        self.assertEqual(c.get(), "spam2")

        ctx1 = contextvars.copy_context()
        self.assertIn(c, ctx1)

        c.reset(t0)
        with pytest.raises(RuntimeError):
            c.reset(t0)
        assert "has already been used" in str(e.value)
        self.assertIsNone(c.get(None))

        self.assertIn(c, ctx1)
        self.assertEqual(ctx1[c], "spam2")
        self.assertEqual(ctx1.get(c, "aa"), "spam2")
        self.assertEqual(len(ctx1), 1)
        self.assertEqual(list(ctx1.items()), [(c, "spam2")])
        self.assertEqual(list(ctx1.values()), ["spam2"])
        self.assertEqual(list(ctx1.keys()), [c])
        self.assertEqual(list(ctx1), [c])

        ctx2 = contextvars.copy_context()
        self.assertNotIn(c, ctx2)
        with self.assertRaises(KeyError):
            ctx2[c]
        self.assertEqual(ctx2.get(c, "aa"), "aa")
        self.assertEqual(len(ctx2), 0)
        self.assertEqual(list(ctx2), [])

    @isolated_context
    def test_context_getset_2(self):
        v1 = contextvars.ContextVar("v1")
        v2 = contextvars.ContextVar("v2")

        t1 = v1.set(42)
        with pytest.raises(ValueError) as e:
            v2.reset(t1)
        assert "by a different" in str(e.value)

    @isolated_context
    def test_context_getset_3(self):
        c = contextvars.ContextVar("c", default=42)
        ctx = contextvars.Context()

        def fun():
            self.assertEqual(c.get(), 42)
            with self.assertRaises(KeyError):
                ctx[c]
            self.assertIsNone(ctx.get(c))
            self.assertEqual(ctx.get(c, "spam"), "spam")
            self.assertNotIn(c, ctx)
            self.assertEqual(list(ctx.keys()), [])

            t = c.set(1)
            self.assertEqual(list(ctx.keys()), [c])
            self.assertEqual(ctx[c], 1)

            c.reset(t)
            self.assertEqual(list(ctx.keys()), [])
            with self.assertRaises(KeyError):
                ctx[c]

        ctx.run(fun)

    @isolated_context
    def test_context_getset_4(self):
        c = contextvars.ContextVar("c", default=42)
        ctx = contextvars.Context()

        tok = ctx.run(c.set, 1)

        with pytest.raises(ValueError) as e:
            c.reset(tok)
        assert "different Context" in str(e.value)

    @isolated_context
    def test_context_getset_5(self):
        c = contextvars.ContextVar("c", default=42)
        c.set([])

        def fun():
            c.set([])
            c.get().append(42)
            self.assertEqual(c.get(), [42])

        contextvars.copy_context().run(fun)
        self.assertEqual(c.get(), [])

    def test_context_copy_1(self):
        ctx1 = contextvars.Context()
        c = contextvars.ContextVar("c", default=42)

        def ctx1_fun():
            c.set(10)

            ctx2 = ctx1.copy()
            self.assertEqual(ctx2[c], 10)

            c.set(20)
            self.assertEqual(ctx1[c], 20)
            self.assertEqual(ctx2[c], 10)

            ctx2.run(ctx2_fun)
            self.assertEqual(ctx1[c], 20)
            self.assertEqual(ctx2[c], 30)

        def ctx2_fun():
            self.assertEqual(c.get(), 10)
            c.set(30)
            self.assertEqual(c.get(), 30)

        ctx1.run(ctx1_fun)

    @isolated_context
    def test_context_threads_1(self):
        cvar = contextvars.ContextVar("cvar")

        def sub(num):
            for i in range(10):
                cvar.set(num + i)
                time.sleep(random.uniform(0.001, 0.05))
                self.assertEqual(cvar.get(), num + i)
            return num

        tp = concurrent.futures.ThreadPoolExecutor(max_workers=10)
        try:
            results = list(tp.map(sub, range(10)))
        finally:
            tp.shutdown()
        self.assertEqual(results, list(range(10)))

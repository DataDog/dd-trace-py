"""Fixture scopes for native per-test line coverage.

A class, module or package fixture's setup runs in its own coverage context, and every test whose
fixture closure contains the fixture gets that context's lines in its record, whichever test ran
the setup. A test skipped before it starts (a skip mark) runs no code, and gets none. Session
fixtures, and the teardown of every fixture wider than a test, run for no test: their lines go to
the process's Shared record.
"""

import types
import typing as t

import pytest

from ddtrace.testing.internal.tracer_api.coverage import collect_child_coverage
from ddtrace.testing.internal.tracer_api.coverage import get_native_coverage_context
from ddtrace.testing.internal.tracer_api.coverage import native_line_coverage_enabled
from ddtrace.testing.internal.tracer_api.coverage import new_fixture_coverage_context
from ddtrace.testing.internal.tracer_api.coverage import set_native_coverage_context


try:
    from _pytest.scope import Scope

    _FUNCTION_SCOPE: t.Any = Scope.Function
except ImportError:  # pytest < 7 has string scopes only
    _FUNCTION_SCOPE = None

_SUITE_SCOPES = frozenset(("class", "module", "package"))
# The coverage context of the fixture's latest setup, which is the instance pytest has cached.
_CONTEXT_ATTR = "_dd_coverage_context"
_PATCHED_ATTR = "_dd_coverage_patched"
# Suite-scope FixtureDefs with a context, from their setup until their finish, in setup order.
# Tests check these rather than their whole fixture closure.
_set_up_with_context: dict[t.Any, None] = {}


def _is_function_scoped(scoped: t.Any) -> bool:
    """Whether a FixtureDef or SubRequest is function-scoped. Most fixtures are, so this is the fast path."""
    scope = getattr(scoped, "_scope", None)
    if scope is not None and _FUNCTION_SCOPE is not None:
        return scope is _FUNCTION_SCOPE
    return bool(scoped.scope == "function")


def _enter_setup_context(scope: str) -> tuple[t.Any, t.Any]:
    """Switch to the context a setup of this scope runs in: its own for a suite scope, none for session."""
    if scope in _SUITE_SCOPES:
        context = new_fixture_coverage_context()
        # The test the setup runs in is suspended until the setup's window closes.
        return context, context.enter()
    previous = get_native_coverage_context()
    set_native_coverage_context(None)
    return None, previous


def link_suite_fixtures_in_closure(item: t.Any) -> None:
    """Make the running test depend on the suite-scope fixtures of its closure that are set up already.

    A test whose setup starts depends on every class, module or package fixture in its fixture
    closure, also one its setup never requests because an earlier fixture failed. FixtureDef.execute
    links each fixture the setup requests, cached or set up anew.
    """
    if not _set_up_with_context:
        return
    user = get_native_coverage_context()
    if user is None:
        return
    info = getattr(item, "_fixtureinfo", None)
    if info is None:
        return
    name2fixturedefs = info.name2fixturedefs
    for fixturedef in _set_up_with_context:
        fixturedefs = name2fixturedefs.get(fixturedef.argname)
        # The last definition is the one this item's requests resolve to.
        if not fixturedefs or fixturedefs[-1] is not fixturedef:
            continue
        context = getattr(fixturedef, _CONTEXT_ATTR, None)
        # pytest clears cached_result when a scope ends, so a cached fixture belongs to this test's scopes.
        if context is not None and fixturedef.cached_result is not None and context not in user.fixtures:
            user.fixtures.append(context)


def _exit_setup_context(fixturedef: t.Any, context: t.Any, previous: t.Any) -> None:
    if context is None:
        set_native_coverage_context(previous)
        return
    # Children that exited inside the setup's window are the setup's.
    collect_child_coverage()
    context.exit(previous)
    setattr(fixturedef, _CONTEXT_ATTR, context)
    _set_up_with_context[fixturedef] = None


class SuiteFixtureLinks:
    """Links each test to the suite-scope fixtures of its closure when its setup starts.

    A plain implementation runs after the skipping plugin's, which raises for a skip mark before
    any fixture is set up, and before the runner's, which sets the fixtures up.
    """

    @pytest.hookimpl  # type: ignore[misc]
    def pytest_runtest_setup(self, item: t.Any) -> None:
        link_suite_fixtures_in_closure(item)


class FixtureCoverage(SuiteFixtureLinks):
    """Also runs each fixture setup wider than a test in the coverage context its scope calls for.

    Only registered on pytest versions without _pytest.fixtures.call_fixture_func: wrapping it
    costs less than a hook wrapper around every fixture setup.
    """

    @pytest.hookimpl(hookwrapper=True)  # type: ignore[misc]
    def pytest_fixture_setup(self, fixturedef: t.Any, request: t.Any) -> t.Generator[None, None, None]:
        if _is_function_scoped(fixturedef) or not native_line_coverage_enabled():
            yield
            return
        context, previous = _enter_setup_context(fixturedef.scope)
        try:
            yield
        finally:
            _exit_setup_context(fixturedef, context, previous)


def install_fixture_tracking() -> t.Optional[SuiteFixtureLinks]:
    """Track fixture scopes for native coverage. Returns the plugin to register.

    Records which suite-scope fixtures each test uses, runs suite setups in their own contexts and
    runs the teardown of wide fixtures for no test. FixtureDef.execute runs for every request of
    a fixture, cached or not, static or through request.getfixturevalue(), so it sees every use.
    No pytest hook does.

    Only fixtures wider than a test get the wrapped execute and finish, on the instance: most
    fixtures are function-scoped, never have a context, and so keep pytest's methods.
    """
    try:
        from _pytest import fixtures
    except ImportError:
        return None
    call_fixture_func = getattr(fixtures, "call_fixture_func", None)
    plugin = SuiteFixtureLinks() if call_fixture_func is not None else FixtureCoverage()
    fixture_def = fixtures.FixtureDef
    if getattr(fixture_def, _PATCHED_ATTR, False):
        return plugin
    init = fixture_def.__init__

    # The wrappers call the class methods as they are at call time, which keeps the patches other
    # plugins apply to them later.
    def execute_recording_use(self: t.Any, request: t.Any) -> t.Any:
        before = getattr(self, _CONTEXT_ATTR, None)
        try:
            return type(self).execute(self, request)
        finally:
            context = getattr(self, _CONTEXT_ATTR, None)
            if context is not None:
                user = get_native_coverage_context()
                if user is not None and user is not context:
                    if before is not None and before is not context and before in user.fixtures:
                        # A new setup (another parameter) replaced the instance linked at test start.
                        user.fixtures.remove(before)
                    if context not in user.fixtures:
                        user.fixtures.append(context)

    def finish_for_no_test(self: t.Any, request: t.Any) -> t.Any:
        if not native_line_coverage_enabled():
            return type(self).finish(self, request)
        previous = get_native_coverage_context()
        set_native_coverage_context(None)
        try:
            return type(self).finish(self, request)
        finally:
            set_native_coverage_context(previous)
            if self.cached_result is None:
                _set_up_with_context.pop(self, None)

    def init_tracking_wide_fixtures(self: t.Any, *args: t.Any, **kwargs: t.Any) -> None:
        init(self, *args, **kwargs)
        if not _is_function_scoped(self):
            self.execute = types.MethodType(execute_recording_use, self)
            self.finish = types.MethodType(finish_for_no_test, self)

    fixture_def.__init__ = init_tracking_wide_fixtures
    setattr(fixture_def, _PATCHED_ATTR, True)

    if call_fixture_func is None:
        return plugin

    # pytest_fixture_setup looks this function up in the module at each setup, so the patch
    # wraps every setup, and only setups: cached fixture values never reach it.
    def call_fixture_func_in_scope_context(fixturefunc: t.Any, request: t.Any, kwargs: t.Any) -> t.Any:
        scope = getattr(request, "_scope", None)
        if scope is not None and scope is _FUNCTION_SCOPE:
            return call_fixture_func(fixturefunc, request, kwargs)
        if _is_function_scoped(request) or not native_line_coverage_enabled():
            return call_fixture_func(fixturefunc, request, kwargs)
        context, previous = _enter_setup_context(request.scope)
        try:
            return call_fixture_func(fixturefunc, request, kwargs)
        finally:
            _exit_setup_context(request._fixturedef, context, previous)

    fixtures.call_fixture_func = call_fixture_func_in_scope_context
    return plugin

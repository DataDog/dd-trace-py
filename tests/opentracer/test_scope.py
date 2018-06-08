import pytest

from ddtrace.opentracer.scope import Scope
from ddtrace.opentracer.scope_manager import ScopeManager
from ddtrace.opentracer.span import Span

# class TestScope(object):
#     def test_init(self):
#         scope = Scope(ScopeManager(), Span(None, None, None))
#         assert scope is not None

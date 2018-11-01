from ddtrace.span import Span


class TestSpanContainer(object):
    def _ensure_test_spans(self, spans):
        return [
            span if isinstance(span, TestSpan) else TestSpan(span) for span in spans
        ]

    @property
    def spans(self):
        raise NotImplementedError

    def _build_tree(self, root):
        children = []
        for span in self.spans:
            if span.parent_id == root.span_id:
                children.append(self._build_tree(span))

        return TestSpanNode(root, children)

    def get_root_span(self):
        root = None
        for span in self.spans:
            if span.parent_id is None:
                if root is not None:
                    raise AssertionError('Multiple root spans found {0!r} {1!r}'.format(root, span))
                root = span

        return self._build_tree(root)

    def assert_span_count(self, count):
        assert len(self.spans) == count, 'Span count {0} != {1}'.format(len(self.spans), count)

    def assert_has_spans(self):
        assert len(self.spans), 'No spans found'

    def assert_has_no_spans(self):
        assert len(self.spans) == 0, 'Span count {0}'.format(len(self.spans))

    def filter_spans(self, *args, **kwargs):
        """
        Helper to filter current spans by provided parameters
        """
        for span in self.spans:
            if span.matches(*args, **kwargs):
                yield span

    def find_span(self, *args, **kwargs):
        span = next(self.filter_spans(*args, **kwargs), None)
        assert span is not None, (
            'No span found for filter {0!r} {1!r}, have {2} spans'
            .format(args, kwargs, len(self.spans))
        )
        return span

class TestSpan(Span):
    def __init__(self, span):
        if isinstance(span, TestSpan):
            span = span._span

        # DEV: Use `object.__setattr__` to by-pass this class's `__setattr__`
        object.__setattr__(self, '_span', span)

    def __getattr__(self, key):
        """
        First look for property on the base :class:`ddtrace.span.Span` otherwise return this object's attribute
        """
        if hasattr(self._span, key):
            return getattr(self._span, key)

        return self.__getattribute__(key)

    def __setattr__(self, key, value):
        """Pass through all assignment to the base :class:`ddtrace.span.Span`"""
        return setattr(self._span, key, value)

    def __eq__(self, other):
        if isinstance(other, TestSpan):
            return other._span == self._span
        elif isinstance(other, Span):
            return other == self._span
        return other == self

    def matches(self, **kwargs):
        for name, value in kwargs.items():
            if getattr(self, name) != value:
                return False

        return True

    def assert_matches(self, **kwargs):
        for name, value in kwargs.items():
            if name == 'meta':
                self.assert_meta(value)
            else:
                assert hasattr(self, name), '{0!r} does not have property {1!r}'.format(self, name)
                assert getattr(self, name) == value, (
                    '{0!r} property {1}: {2!r} != {3!r}'
                    .format(self, name, getattr(self, name), value)
                )

    def assert_meta(self, meta, exact=False):
        if exact:
            assert self.meta == meta
        else:
            for key, value in meta.items():
                assert key in self.meta, '{0} meta does not have property {1!r}'.format(self, key)
                assert self.meta[key] == value, (
                    '{0} meta property {1!r}: {2!r} != {3!r}'
                    .format(self, key, self.meta[key], value)
                )


class TestSpanNode(TestSpan, TestSpanContainer):
    def __init__(self, root, children=None):
        super(TestSpanNode, self).__init__(root)
        object.__setattr__(self, '_children', children or [])

    @property
    def spans(self):
        return self._children

    def assert_structure(self, root, children):
        self.assert_matches(**root)

        # Give them a way to ignore asserting on children
        if children is None:
            return

        spans = self.spans
        self.assert_span_count(len(children))
        for i, (root, _children) in enumerate(children):
            spans[i].assert_matches(parent_id=self.span_id, trace_id=self.trace_id, _parent=self)
            spans[i].assert_structure(root, _children)

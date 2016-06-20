
import logging
import random
import time


log = logging.getLogger(__name__)


class Span(object):

    def __init__(self,
            tracer,
            name,

            service=None,
            resource=None,
            span_type=None,

            trace_id=None,
            span_id=None,
            parent_id=None,
            start=None):
        """
        tracer: a link to the tracer that will store this span
        name: the name of the operation we're measuring.
        service: the name of the service that is being measured
        resource: an optional way of specifying the 'normalized' params
                  of the request (i.e. the sql query, the url handler, etc)
        start: the start time of request as a unix epoch in seconds
        """
        # required span info
        self.name = name
        self.service = service
        self.resource = resource or name
        self.span_type = span_type

        # tags / metatdata
        self.meta = {}
        self.error = 0
        self.metrics = {}

        # timing
        self.start = start or time.time()
        self.duration = None

        # tracing
        self.trace_id = trace_id or _new_id()
        self.span_id = span_id   or _new_id()
        self.parent_id = parent_id

        self._tracer = tracer
        self._parent = None

    def finish(self, finish_time=None):
        """ Mark the end time of the span and submit it to the tracer. """
        ft = finish_time or time.time()
        # be defensive so we don't die if start isn't set
        self.duration = ft - (self.start or ft)
        if self._tracer:
            self._tracer.record(self)

    def to_dict(self):
        """ Return a json serializable dictionary of the span's attributes. """
        d = {
            'trace_id' : self.trace_id,
            'parent_id' : self.parent_id,
            'span_id' : self.span_id,
            'service': self.service,
            'resource' : self.resource,
            'name' : self.name,
            'error': self.error,
        }

        if self.start:
            d['start'] = int(self.start * 1e9)  # ns

        if self.duration:
            d['duration'] = int(self.duration * 1e9)  # ns

        if self.meta:
            d['meta'] = self.meta

        if self.span_type:
            d['type'] = self.span_type

        return d

    def set_tag(self, key, value):
        """ Set the given key / value tag pair on the span. Keys and values
            must be strings (or stringable). If a casting error occurs, it will
            be ignored.
        """
        try:
            self.meta[key] = unicode(value)
        except Exception:
            log.warn("error setting tag. ignoring", exc_info=True)

    def get_tag(self, key):
        """ Return the given tag or None if it doesn't exist"""
        return self.meta.get(key, None)

    def set_tags(self, tags):
        """ Set a dictionary of tags on the given span. Keys and values
            must be strings (or stringable)
        """
        if tags:
            for k, v in tags.iteritems():
                self.set_tag(k, v)

    # backwards compatilibility, kill this
    set_meta = set_tag
    set_metas = set_tags

    def pprint(self):
        """ Return a human readable version of the span. """
        lines = [
            ("id", self.span_id),
            ("trace_id", self.trace_id),
            ("parent_id", self.parent_id),
            ("service", self.service),
            ("resource", self.resource),
            ("start", self.start),
            ("end", "" if not self.duration else self.start + self.duration),
            ("duration", self.duration),
            ("error", self.error),
        ]

        return "\n".join("%10s %s" % l for l in lines)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.error = 1
            # FIXME[matt] store traceback info
        self.finish()

    def __repr__(self):
        return "<Span(id=%s,trace_id=%s,parent_id=%s,name=%s)>" % (
                self.span_id,
                self.trace_id,
                self.parent_id,
                self.name,
        )

def _new_id():
    """Generate a random trace_id"""
    return random.getrandbits(63)


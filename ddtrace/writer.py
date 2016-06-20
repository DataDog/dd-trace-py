
from .reporter import AgentReporter


class Writer(object):

    def write(self, spans):
        raise NotImplementedError()


class NullWriter(Writer):

    def write(self, spans):
        pass


class AgentWriter(Writer):

    def __init__(self):
        self._reporter = AgentReporter()
        self.enabled = True # flip this to disable on the fly

    def write(self, spans):
        if self.enabled:
            self._reporter.report(spans, [])

from .reporter import AgentReporter


class AgentWriter(object):

    def __init__(self):
        self._reporter = AgentReporter()

    def write(self, spans):
        self._reporter.report(spans, [])

from .reporter import AgentReporter


class AgentWriter(object):

    def __init__(self, hostname='localhost', port=7777):
        self._reporter = AgentReporter(hostname, port)

    def write(self, spans, services=None):
        self._reporter.report(spans, services)


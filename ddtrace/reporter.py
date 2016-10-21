import logging

# project
from .transport import HTTPTransport
from .encoding import encode_spans, encode_services
from .workers import AsyncTransport


log = logging.getLogger(__name__)


class AgentReporter(object):
    """
    Report spans to the Agent API.
    """
    def __init__(self, hostname, port, buffer_size=1000, flush_interval=1):
        self._transport = HTTPTransport(hostname, port)
        self._services = {}
        self._workers = []

        # start threads that communicate with the trace agent
        worker = AsyncTransport(flush_interval, self.send_traces, self._transport, buffer_size)
        self._workers.append(worker)
        self.start()

    def start(self):
        """
        Start background workers that send traces and service metadata to the trace agent
        """
        for worker in self._workers:
            worker.start()

    def stop(self):
        """
        Stop background workers. This operation is synchronous and so you should not use
        it in your application code otherwise it could hang your code execution.
        """
        for worker in self._workers:
            worker.stop()

    def report(self, trace, services):
        if trace:
            trace_worker = self._workers[0]
            trace_worker.queue(trace)

        if services:
            # TODO[manu]: services push are disabled at the moment
            # self._services.update(services)
            pass

    def send_traces(self, spans, transport):
        log.debug('Reporting {} spans'.format(len(spans)))
        payload = encode_spans(spans)
        transport.send("PUT", "/spans", payload)

    def send_services(self, services, transport):
        log.debug('Reporting {} services'.format(len(services)))
        payload = encode_services(services)
        transport.send("PUT", "/services", payload)

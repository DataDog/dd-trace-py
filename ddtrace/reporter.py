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
    def __init__(self, hostname, port, buffer_size=1000, flush_interval=1, service_interval=120):
        self._transport = HTTPTransport(hostname, port)
        self._workers = []

        # Asynchronous workers that send data to the trace agent
        traces = AsyncTransport(flush_interval, self.send_traces, self._transport, buffer_size)
        services = AsyncTransport(service_interval, self.send_traces, self._transport, 1)
        self._workers.append(traces)
        self._workers.append(services)
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

    def write_trace(self, trace):
        trace_worker = self._workers[0]
        trace_worker.queue(trace)

    def write_service(self, services):
        service_worker = self._workers[1]
        service_worker.queue(services)

    def send_traces(self, spans, transport):
        log.debug('Reporting {} spans'.format(len(spans)))
        payload = encode_spans(spans)
        transport.send("PUT", "/spans", payload)

    def send_services(self, services, transport):
        log.debug('Reporting {} services'.format(len(services)))
        payload = encode_services(services)
        transport.send("PUT", "/services", payload)

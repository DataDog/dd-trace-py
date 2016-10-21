import logging

# project
from .transport import HTTPTransport
from .encoding import encode_spans, encode_services
from .workers import AsyncTransport


log = logging.getLogger(__name__)


class AgentWriter(object):
    """
    Report spans to the Agent API.
    """
    def __init__(self, hostname='localhost', port=7777, buffer_size=1000, flush_interval=1, service_interval=120):
        self._transport = HTTPTransport(hostname, port)
        self._workers = []

        # Asynchronous workers that send data to the trace agent
        traces = AsyncTransport(flush_interval, self.send_traces, self._transport, buffer_size)
        services = AsyncTransport(service_interval, self.send_services, self._transport, 1)
        self._workers.append(traces)
        self._workers.append(services)

    def start(self):
        """
        Start background workers that send traces and service metadata to the trace agent.
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
        # extract the services dictionary
        services = services[0]
        # keep the services list for the next call, so that even if we have
        # communication problems (i.e. the trace agent isn't started yet) we
        # can resend the payload every ``service_interval`` seconds
        service_worker = self._workers[1]
        service_worker.queue(services)
        # encode and send services
        log.debug('Reporting {} services'.format(len(services)))
        payload = encode_services(services)
        transport.send("PUT", "/services", payload)

from datetime import datetime
import logging
import socket

from ddtrace import tracer
from nameko.extensions import DependencyProvider

from .util import SERVICE


logger = logging.getLogger(__name__)


class DDTracer(DependencyProvider):
    """ Entrypoint DD tracer dependency
    """

    def __init__(self):
        self.logger = None
        self.worker_timestamps = {}

    def setup(self):
        self.logger = logging.getLogger('dd_tracer')

    @tracer.wrap(service=SERVICE)
    def worker_setup(self, worker_ctx):
        """ Trace entrypoint call details
        """

        timestamp = datetime.utcnow()
        self.worker_timestamps[worker_ctx] = timestamp

        try:
            extra = {
                'stage': 'request',
                'worker_ctx': worker_ctx,
                'timestamp': timestamp,
                'hostname': socket.gethostname(),
                'entrypoint_type': type(worker_ctx.entrypoint)
            }
            with tracer.trace("[%s] entrypoint call trace".format(worker_ctx.call_id)) as span:
                for k, v in extra.iteritems():
                    span.set_meta(k, str(v))

        except Exception:
            logger.warning('Failed to log entrypoint trace', exc_info=True)

    @tracer.wrap(service=SERVICE)
    def worker_result(self, worker_ctx, result=None, exc_info=None):
        """ Trace entrypoint result details
        """

        timestamp = datetime.utcnow()
        worker_setup_timestamp = self.worker_timestamps[worker_ctx]
        response_time = (timestamp - worker_setup_timestamp).total_seconds()

        try:
            extra = {
                'stage': 'response',
                'worker_ctx': worker_ctx,
                'result': result,
                'exc_info_': exc_info,
                'timestamp': timestamp,
                'response_time': response_time,
                'hostname': socket.gethostname(),
                'entrypoint_type': type(worker_ctx.entrypoint)
            }

            with tracer.trace("[%s] entrypoint result trace".format(worker_ctx.call_id)) as span:
                for k, v in extra.iteritems():
                    span.set_meta(k, str(v))

        except Exception:
            logger.warning('Failed to log entrypoint trace', exc_info=True)


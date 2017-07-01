import asyncio
import inspect
import flask
import functools
import moto.server
import threading
import requests
import time

import aiobotocore.session
from ddtrace import Pin
from contextlib import contextmanager


MOTO_PORT = 5000
MOTO_HOST = '127.0.0.1'
MOTO_ENDPOINT_URL = 'http://{}:{}'.format(MOTO_HOST, MOTO_PORT)

_proxy_bypass = {
    "http": None,
    "https": None,
}


@contextmanager
def aiobotocore_client(service, tracer):
    """Helper function that creates a new aiobotocore client so that
    it is closed at the end of the context manager.
    """
    session = aiobotocore.session.get_session()
    client = session.create_client(service, region_name='us-west-2', endpoint_url=MOTO_ENDPOINT_URL)
    Pin.override(client, tracer=tracer)
    try:
        yield client
    finally:
        client.close()


class MotoService:
    def __init__(self, service_name):
        self._service_name = service_name
        self._thread = None

    def __call__(self, func):
        if inspect.isgeneratorfunction(func):
            @asyncio.coroutine
            def wrapper(*args, **kwargs):
                self._start()
                try:
                    result = yield from func(*args, **kwargs)
                finally:
                    self._stop()
                return result
        else:
            def wrapper(*args, **kwargs):
                self._start()
                try:
                    result = func(*args, **kwargs)
                finally:
                    self._stop()
                return result

        functools.update_wrapper(wrapper, func)
        wrapper.__wrapped__ = func
        return wrapper

    def _shutdown(self):
        req = flask.request
        shutdown = req.environ['werkzeug.server.shutdown']
        shutdown()
        return flask.make_response('done', 200)

    def _create_backend_app(self, *args, **kwargs):
        backend_app = moto.server.create_backend_app(*args, **kwargs)
        backend_app.add_url_rule('/shutdown', 'shutdown', self._shutdown)
        return backend_app

    def _server_entry(self):
        main_app = moto.server.DomainDispatcherApplication(
            self._create_backend_app, service=self._service_name)
        main_app.debug = True

        moto.server.run_simple(MOTO_HOST, MOTO_PORT, main_app, threaded=True)

    def _start(self):
        self._thread = threading.Thread(target=self._server_entry, daemon=True)
        self._thread.start()

        for i in range(0, 10):
            if not self._thread.is_alive():
                break

            try:
                # we need to bypass the proxies due to monkeypatches
                requests.get(MOTO_ENDPOINT_URL + '/static/',
                             timeout=0.5, proxies=_proxy_bypass)
                break
            except requests.exceptions.ConnectionError:
                time.sleep(0.5)
        else:
            self._stop()  # pytest.fail doesn't call stop_process
            raise Exception("Can not start service: {}".format(self._service_name))

    def _stop(self):
        try:
            requests.get(MOTO_ENDPOINT_URL + '/shutdown',
                         timeout=5, proxies=_proxy_bypass)
        except:
            import traceback
            traceback.print_exc()
        finally:
            self._thread.join()

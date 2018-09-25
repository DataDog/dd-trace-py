import influxdb  # version >=5.0
import wrapt
from influxdb.exceptions import InfluxDBClientError, InfluxDBServerError

from . import metadata

from ...utils.wrappers import unwrap
from ...compat import urlencode
from ...pin import Pin
from ...ext import http, db, AppTypes


DEFAULT_SERVICE = 'influxdb'
SPAN_TYPE = 'sql'


def patch():
    if getattr(influxdb, '_datadog_patch', False):
        return
    setattr(influxdb, '_datadog_patch', True)
    wrapt.wrap_function_wrapper('influxdb.client', 'InfluxDBClient.request', _request)

    Pin(service=DEFAULT_SERVICE, app=DEFAULT_SERVICE, app_type=AppTypes.db).onto(influxdb.client.InfluxDBClient)


def unpatch():
    if getattr(influxdb, '_datadog_patch', False):
        setattr(influxdb, '_datadog_patch', False)
        unwrap(influxdb.client.InfluxDBClient, 'request')


def _request(func, instance, args, kwargs):
    """
    Trace a request to InfluxDB at the level of a (retried) HTTP request.
    :param func: A reference to influxdb.client.InfluxDBClient.request, called.
    :param instance: An instance of influxdb.client.InfluxDBClient.
    :param args: Arguemnts passed to influxdb.client.InfluxDBClient.request()
    :param kwargs: Keyword arguments passed to influxdb.client.InfluxDBClient.request.
    :return: An instance of requests.models.Response
    """

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    with pin.tracer.trace('influx.request') as span:
        # Don't instrument if the trace is not sampled
        if not span.sampled:
            return func(*args, **kwargs)

        span.service = pin.service
        span.span_type = SPAN_TYPE

        url = kwargs.get('url')
        span.set_tag(http.URL, url)

        params = kwargs.get('params', {})

        database = params.get('db', None)
        if database:
            span.set_tag(db.NAME, database)

        method = kwargs.get('method', 'GET').upper()
        if method == 'GET' and url.startswith('query'):
            span.resource = params.get('q', None)
        elif url:
            span.resource = url

        span.set_tag(http.METHOD, method)
        span.set_tag(metadata.PARAMS, urlencode(params))

        try:
            result = func(*args, **kwargs)
        except InfluxDBClientError as e:
            span.set_tag(http.STATUS_CODE, getattr(e, 'code', 400))
            raise
        except InfluxDBServerError as e:
            span.set_tag(http.STATUS_CODE, getattr(e, 'code', 500))
            raise

        span.set_tag(http.STATUS_CODE, result.status_code)

        return result

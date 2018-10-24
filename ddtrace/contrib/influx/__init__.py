"""

The InfluxDB integration will trace all requests made to an InfluxDB backend.
To trace your application, call the patch method::

    from ddtrace import patch
    from influxdb import InfluxDBClient

    # If not patched yet, you can patch InfluxDBClient explicitly
    patch(influx=True)

    # This will report spans with the default instrumentation
    influx_connection = InfluxDBClient(host="influxdb.example.com")
    # Example of instrumented query
    r = influx_connection.query(
        'SELECT * from average_temperature where time > 1439857080000000000 limit 10',
        database='NOAA_water_database'
    )


To change the InfluxDB service name, you can use the ``Config`` API as follows::

    from ddtrace import config

    # change service names for producers and workers
    config.influx['service_name'] = 'tsdb'
    config.influx['app_name'] = 'water'

"""
from ...utils.importlib import require_modules

required_modules = ['influxdb']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch
        __all__ = ['patch']

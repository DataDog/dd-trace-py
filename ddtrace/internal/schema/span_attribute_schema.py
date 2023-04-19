SPAN_ATTRIBUTE_SCHEMA_VERSIONS = dict()

_DEFAULT_SPAN_SERVICE_NAMES = {"v0": None, "v1": "unnamed-python-service"}


def register_schema_version(version):
    def _wrap(func):
        assert version not in SPAN_ATTRIBUTE_SCHEMA_VERSIONS, "duplicate version entry for {}".format(version)
        SPAN_ATTRIBUTE_SCHEMA_VERSIONS[version] = func

    return _wrap


@register_schema_version("v0")
def service_name_v0(v0_service_name):
    return v0_service_name


@register_schema_version("v1")
def service_name_v1(*_, **__):
    from ddtrace import config as dd_config

    return dd_config.service

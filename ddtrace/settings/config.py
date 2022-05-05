import os

from ..internal.logger import get_logger
from ..internal.utils.formats import asbool
from ..internal.utils.formats import parse_tags_str


log = get_logger(__name__)


class Config(object):
    """Shared configuration object."""

    def __init__(self):
        self.tags = parse_tags_str(os.getenv("DD_TAGS") or "")

        self.env = os.getenv("DD_ENV") or self.tags.get("env")
        self.service = os.getenv("DD_SERVICE", default=self.tags.get("service"))
        self.version = os.getenv("DD_VERSION", default=self.tags.get("version"))

        self.service_mapping = parse_tags_str(os.getenv("DD_SERVICE_MAPPING", default=""))

        # The service tag corresponds to span.service and should not be
        # included in the global tags.
        if self.service and "service" in self.tags:
            del self.tags["service"]

        # The version tag should not be included on all spans.
        if self.version and "version" in self.tags:
            del self.tags["version"]

        # Raise certain errors only if in testing raise mode to prevent crashing in production with non-critical errors
        self._raise = asbool(os.getenv("DD_TESTING_RAISE", False))
        # TODO: move?
        self._appsec_enabled = asbool(os.getenv("DD_APPSEC_ENABLED", False))

    def __repr__(self):
        cls = self.__class__
        values = ", ".join(("%s=%r" % (k, v) for k, v in self.__dict__.items()))
        return "{}.{}({})".format(cls.__module__, cls.__name__, values)


config = Config()

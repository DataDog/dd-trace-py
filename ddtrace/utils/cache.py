from ..internal.utils.cache import CachedMethodDescriptor  # noqa
from ..internal.utils.cache import F  # noqa
from ..internal.utils.cache import M  # noqa
from ..internal.utils.cache import S  # noqa
from ..internal.utils.cache import T  # noqa
from ..internal.utils.cache import cached  # noqa
from ..internal.utils.cache import cachedmethod  # noqa
from ..internal.utils.cache import miss  # noqa
from ..internal.utils.deprecation import deprecation


deprecation(
    name="ddtrace.utils.cache",
    message="This module will be removed in v1.0.",
    version="1.0.0",
)

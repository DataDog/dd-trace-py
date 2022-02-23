import base64
from typing import Optional
from typing import Text
from typing import Union

from ..compat import ensure_binary
from ..compat import ensure_text
from ..constants import SamplingMechanism
from ..constants import UNNAMED_SERVICE_NAME
from .cache import cached


_ServiceName = Union[Text, bytes]


# DEV: The number of services, especially sampling services should be low, usually only 1
@cached(maxsize=24)
def _format_service_name(service):
    # type: (_ServiceName) -> str
    if not service:
        service = UNNAMED_SERVICE_NAME

    # base64 encode binary representation of the service name
    return ensure_text(base64.b64encode(ensure_binary(service, errors="backslashreplace")))


def _format_sample_rate(sample_rate):
    # type: (Optional[float]) -> str
    if sample_rate is None:
        return ""

    return "{:0.4f}".format(sample_rate)


def format_upstream_service_entry(
    service,  # type: Optional[_ServiceName]
    sampling_priority,  # type: int
    sampling_mechanism,  # type: SamplingMechanism
    sample_rate,  # type: Optional[float]
):
    # type: (...) -> str
    """
    Generate an ``_dd.p.upstream_services`` entry.

    ``<base64 encoded service name>|<sampling priority>|<sampling mechanism>|<sample rate or "">``

    If no service name is provided, then ``"unnamed_python_service"`` is used.

    ``_dd.p.upstream_services`` eBNF::

        upstream services  =  ( group, { ";", group } ) | "";

        group  =  service name, "|",
                  sampling priority, "|",
                  sampling mechanism, "|",
                  sampling rate, { "|", future field };

        service name  =  ? base64 encoded UTF-8 bytes ?;

        (* no plus signs, no exponential notation, etc. *)
        sampling priority  =  ? decimal integer ? ;

        sampling mechanism  =  ? positive decimal integer ?;

        sampling rate  =  "" | normalized float;

        normalized float  =  ? decimal between 0.0 and 1.0 inclusive with at most four significant digits (rounded up)
                               and with the leading "0." e.g. "0.9877" ?;

        (* somewhat arbitrarily based on the other grammar *)
        future field  =  ( ? ASCII characters 32-126 ? - delimiter );

        delimiter  =  "|" | ","
    """

    return "|".join(
        [
            _format_service_name(service),
            str(sampling_priority),
            str(sampling_mechanism.value),
            _format_sample_rate(sample_rate),
        ]
    )

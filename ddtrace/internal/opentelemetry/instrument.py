from opentelemetry.metrics import Counter as OtelApiCounter
from opentelemetry.metrics import Histogram as OtelApiHistogram
from opentelemetry.metrics import Meter as OtelApiMeter
from opentelemetry.metrics import MeterProvider as APIMeterProvider
from opentelemetry.metrics import ObservableCounter as OtelApiObservableCounter
from opentelemetry.metrics import ObservableGauge as OtelApiObservableGauge
from opentelemetry.metrics import (
    ObservableUpDownCounter as OtelApiObservableUpDownCounter,
)
from opentelemetry.metrics import UpDownCounter as OtelApiUpDownCounter
from opentelemetry.metrics import _Gauge as OtelApiGauge

from ddtrace.internal.logger import get_logger

log = get_logger(__name__)

class Counter(OtelApiCounter):
    def __init__(self, client, name, unit = "", description = ""):
        super().__init__(name, unit=unit, description=description)
        self._client = client
        self.name = name
        self.unit = unit
        self.description = description

    def add(
        self,
        amount,
        attributes = None,
        context = None,
    ) -> None:
        if amount < 0:
            log.warning(
                "Add amount must be non-negative on Counter %s.", self.name
            )
            return
        
        # Calling DogStatsD for the POC
        self._client.increment(
            self.name, amount, [":".join(_) for _ in attributes.items()] if attributes else None
        )

class UpDownCounter(OtelApiUpDownCounter):
    def __init__(self, client, name, unit = "", description = ""):
        super().__init__(name, unit=unit, description=description)
        self._client = client
        self.name = name
        self.unit = unit
        self.description = description

    def add(
        self,
        amount,
        attributes = None,
        context = None,
    ) -> None:
        # Calling DogStatsD for the POC
        self._client.increment(
            self.name, amount, [":".join(_) for _ in attributes.items()] if attributes else None
        )

class Gauge(OtelApiGauge):
    def __init__(self, client, name, unit = "", description = ""):
        super().__init__(name, unit=unit, description=description)
        self._client = client
        self.name = name
        self.unit = unit
        self.description = description

    def set(
        self,
        amount,
        attributes = None,
        context = None,
    ) -> None:
        # Calling DogStatsD for the POC
        self._client.gauge(
            self.name, amount, [":".join(_) for _ in attributes.items()] if attributes else None
        )

class ObservableCounter(OtelApiObservableCounter):
    def __init__(self, client, name, unit = "", description = ""):
        super().__init__(name, unit=unit, description=description)
        self._client = client

class ObservableUpDownCounter(OtelApiObservableUpDownCounter):
    def __init__(self, client, name, unit = "", description = ""):
        super().__init__(name, unit=unit, description=description)
        self._client = client

class ObservableGauge(OtelApiObservableGauge):
    def __init__(self, client, name, callbacks = None, unit = "", description = ""):
        super().__init__(name, callbacks, unit=unit, description=description)
        self._client = client

class Histogram(OtelApiHistogram):
    def __init__(self, client, name, unit = "", description = ""):
        super().__init__(name, unit=unit, description=description, explicit_bucket_boundaries_advisory=None)
        self._client = client
        self.name = name
        self.unit = unit
        self.description = description

    def record(
        self,
        amount,
        attributes = None,
        context = None,
    ) -> None:
        if amount < 0:
            log.warning(
                "Add amount must be non-negative on Counter %s.", self.name
            )
            return
        
        # Calling DogStatsD for the POC
        self._client.distribution(
            self.name, amount, [":".join(_) for _ in attributes.items()] if attributes else None
        )    
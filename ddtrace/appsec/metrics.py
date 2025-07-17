from dataclasses import dataclass


@dataclass
class MetricsDict:
    json_deser_time_ns: int = 0
    waf_init_time_ns: int = 0
    object_creation_time_ns: int = 0
    appsec_init_time: int = 0

    def to_json(self) -> dict:
        """Convert the metrics to a JSON-serializable dictionary."""
        # Convert ns to ms as floats for better readability
        return {
            "json_deser_time_ms": self.json_deser_time_ns / 1_000_000,
            "waf_init_time_ms": self.waf_init_time_ns / 1_000_000,
            "object_creation_time_ms": self.object_creation_time_ns / 1_000_000,
            "appsec_init_time_ms": self.appsec_init_time / 1_000_000,
        }


APPSEC_TEMP_METRICS: MetricsDict = MetricsDict()

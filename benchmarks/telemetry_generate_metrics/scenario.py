from bm import Scenario

from ddtrace.internal.telemetry.writer import TelemetryWriter

class TelemetryGenerateMetrics(Scenario):
    """
    This scenario checks to see if there's an impact on generating the metrics event via instrumentation telemetry
    """
    runs: int
    def run(self):
        def _(loops):
            for _ in range(loops):
                for i in range(self.runs):
                    telemetrywriter = TelemetryWriter()
                    telemetrywriter._generate_metrics_event(telemetrywriter._namespace.flush())
        
        yield _
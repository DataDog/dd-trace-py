class NoOpTelemetryWriter(object):
    def __getattr__(self, name):
        return self.noop

    def noop(self, *args, **kwargs):
        pass

class NoOpTelemetryWriter(object):
    def __init__(self, *args, **kwargs):
        super(NoOpTelemetryWriter, self).__init__()

    def __getattr__(self, name):
        return self.noop

    def noop(self, *args, **kwargs):
        pass

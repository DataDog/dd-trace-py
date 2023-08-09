class MockConnector:
    data = None
    metadata = None

    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data

    def write(self, metadata, config_raw):
        self.data = config_raw
        self.metadata = metadata

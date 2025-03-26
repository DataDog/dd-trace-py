class MockConnector:
    data = None

    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data

    def write(self, payload_list):
        self.data = payload_list

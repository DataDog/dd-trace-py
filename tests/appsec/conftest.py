import pytest


class DummyEventWriter:
    def __init__(self):
        self.events = []

    def write(self, events):
        self.events.extend(events)

    def flush(self, timeout=None):
        pass


@pytest.fixture
def appsec_dummy_writer(appsec):
    appsec.enable()
    appsec._mgmt._writer = writer = DummyEventWriter()
    yield writer

import dramatiq
from dramatiq import Worker
from dramatiq.brokers.stub import StubBroker
import pytest


@pytest.fixture()
def stub_broker():
    broker = StubBroker()
    broker.emit_after("process_boot")
    dramatiq.set_broker(broker)
    yield broker
    broker.flush_all()
    broker.close()


@pytest.fixture()
def stub_worker(stub_broker):
    worker = Worker(stub_broker)
    worker.start()
    yield worker
    worker.stop()

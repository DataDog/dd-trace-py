import dramatiq
from dramatiq import Worker
from dramatiq.brokers.stub import StubBroker
import pytest


@pytest.fixture()
def stub_broker():
    previous_broker = dramatiq.get_broker()
    broker = StubBroker()
    broker.emit_after("process_boot")
    dramatiq.set_broker(broker)
    try:
        yield broker
    finally:
        dramatiq.set_broker(previous_broker)
        broker.flush_all()
        broker.close()


@pytest.fixture()
def stub_worker(stub_broker):
    worker = Worker(stub_broker)
    worker.start()
    yield worker
    worker.stop()

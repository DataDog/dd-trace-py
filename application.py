from ddtrace import tracer
import ddtrace
ddtrace.patch_all()
from confluent_kafka import Producer, Consumer, KafkaError, KafkaException
import time
import os
import signal
import sys

##################

def delivery_report(err, msg):
    if err is not None:
        print(f"Message delivery failed: {err}")
    else:
        print(f"Message delivered to {msg.topic()} [{msg.partition()}]")

servers = os.environ.get('KAFKA_BOOTSTRAP_SERVERS', '')
print(os.environ.get('DD_SERVICE_NAME', ''))
print(os.environ.get('DD_DATA_STREAMS_ENABLED', ''))
TOPIC1 = "python-topic-1"
TOPIC2 = "python-topic-2"

if os.environ.get('APP_ROLE') == "CONSUMER":
    print("Starting in consumer mode!")
    # Set up Kafka consumer
    consumer = Consumer(
        {
            'bootstrap.servers': servers,
            'group.id': 'my_group',
            'enable.auto.commit': True,
            'auto.offset.reset': 'earliest',
        })
    def signal_handler(sig, frame):
        print("Received signal to exit. Closing Kafka consumer...")
        consumer.close()
        sys.exit(0)

    # Register the signal handler function for SIGTERM signals
    signal.signal(signal.SIGTERM, signal_handler)
    try:
        consumer.subscribe([TOPIC2])
        while True:
            with tracer.start_span('consume'):
                msg = consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        # End of partition event
                        sys.stderr.write('%% %s [%d] reached end at offset %d\n' % (msg.topic(), msg.partition(), msg.offset()))
                    elif msg.error():
                        raise KafkaException(msg.error())
                else:
                    print(f"Received message: {msg.value()}")
                    sys.stdout.flush()
    finally:
        # Close down consumer to commit final offsets.
        consumer.close()
elif os.environ.get('APP_ROLE') == "PRODUCER":
    print("Starting in producer mode!")
    # Create a Kafka producer
    producer = Producer({
        'bootstrap.servers': servers,
        'client.id': "python-producer"
    })

    # Define a signal handler function to exit gracefully when the pod is terminated
    def signal_handler(sig, frame):
        print("Received signal to exit. Closing Kafka producer...")
        producer.close()
        sys.exit(0)

    # Register the signal handler function for SIGTERM signals
    signal.signal(signal.SIGTERM, signal_handler)

    # Produce messages in a loop forever
    while True:
        with tracer.start_span('produce'):
            # Check if the pod has been terminated before producing the message
            if os.path.exists('/tmp/stop_signal'):
                print("Received signal to exit. Closing Kafka producer...")
                producer.close()
                sys.exit(0)
            message = b"Hello, Kafka!"
            producer.produce(TOPIC1, value=message, callback=delivery_report)
            print("Message sent!")
            sys.stdout.flush()
            time.sleep(1)
else:
    print("Starting in forwarding mode")
    # Create a Kafka producer
    producer = Producer({
        'bootstrap.servers': servers,
        'client.id': "python-producer"
    })

    consumer = Consumer(
        {
            'bootstrap.servers': servers,
            'group.id': 'my_group',
            'enable.auto.commit': True,
            'auto.offset.reset': 'earliest',
        })
    def signal_handler(sig, frame):
        print("Received signal to exit. Closing Kafka consumer...")
        consumer.close()
        producer.close()
        sys.exit(0)

    # Register the signal handler function for SIGTERM signals
    signal.signal(signal.SIGTERM, signal_handler)
    try:
        consumer.subscribe([TOPIC1])
        while True:
            with tracer.start_span('consume'):
                msg = consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        # End of partition event
                        sys.stderr.write('%% %s [%d] reached end at offset %d\n' % (msg.topic(), msg.partition(), msg.offset()))
                    elif msg.error():
                        raise KafkaException(msg.error())
                else:
                    producer.produce(TOPIC2, value=msg.value(), callback=delivery_report)
                    print(f"Forward message: {msg.value()}")
                    sys.stdout.flush()
    finally:
        # Close down consumer to commit final offsets.
        consumer.close()



import ray


ray.init()


@ray.remote
class Sender:
    def __init__(self):
        self.sent_count = 0

    def send_message(self, receiver, message):
        self.sent_count += 1
        future = receiver.receive_message.remote(message)
        return ray.get(future)

    def get_sent_count(self):
        return self.sent_count


@ray.remote
class Receiver:
    def __init__(self):
        self.received_messages = []

    def receive_message(self, message):
        self.received_messages.append(message)
        return f"received: {message}"

    def get_messages(self):
        return self.received_messages


def main():
    sender = Sender.remote()
    receiver = Receiver.remote()

    result = ray.get(sender.send_message.remote(receiver, "hello"))
    sent_count = ray.get(sender.get_sent_count.remote())
    messages = ray.get(receiver.get_messages.remote())

    assert result == "received: hello"
    assert sent_count == 1
    assert messages == ["hello"]


if __name__ == "__main__":
    main()
    ray.shutdown()

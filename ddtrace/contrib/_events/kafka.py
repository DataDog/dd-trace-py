from dataclasses import dataclass
from typing import Any
from typing import Optional

from ddtrace.contrib._events.messaging import MessagingEvent
from ddtrace.contrib._events.messaging import MessagingProcessEvent
from ddtrace.contrib._events.messaging import MessagingProducerEvent
from ddtrace.ext.kafka import GROUP_ID
from ddtrace.ext.kafka import HOST_LIST
from ddtrace.ext.kafka import SERVICE
from ddtrace.ext.kafka import TOPIC
from ddtrace.internal.constants import MESSAGING_DESTINATION_NAME
from ddtrace.internal.constants import MESSAGING_SYSTEM
from ddtrace.internal.core.events import event_field


@dataclass
class KafkaEvent(MessagingEvent):
    topic: Optional[str] = event_field(default=None)
    bootstrap_servers: Any = event_field(default=None)

    def __post_init__(self) -> None:
        super().__post_init__()
        self.tags[MESSAGING_SYSTEM] = SERVICE
        if self.topic is not None:
            self.tags[TOPIC] = self.topic
            if self.topic:
                self.tags[MESSAGING_DESTINATION_NAME] = self.topic
        if self.bootstrap_servers is not None:
            self.tags[HOST_LIST] = self.bootstrap_servers


@dataclass
class KafkaProducerEvent(MessagingProducerEvent, KafkaEvent):
    pass


@dataclass
class KafkaProcessEvent(MessagingProcessEvent, KafkaEvent):
    group_id: Optional[str] = event_field(default=None)

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.group_id is not None:
            self.tags[GROUP_ID] = self.group_id

"""
The Datadog Remote Configuration Publisher-Subscriber system.

A common Python web application use to execute a WSGI server (e.x: Gunicorn) and this server use many workers.

Remote Configuration needs to keep all workers updated as soon as possible. Therefore, Remote Configuration may start
BEFORE the Gunicorn server in sitecustomize.py, it starts to poll information from the RC Agent, and for each new
payload, through this Pub-sub system, share this information with all child processes.

In addition to this, there are different Remote Configuration behaviors:

- When the Remote Configuration Client receives a new product target file payload, we need to call a callback.
- When the Remote Configuration Client receives a new product target file payload, we need to aggregate this target
  file data for each product. After that, call the callback with all aggregated information.
- Remote Configuration may have a callback for each product.
- Remote Configuration may have a callback for one or more products.
- For each payload, Remote Configuration needs to execute specific actions on the main process and a different action
  on child processes.

To achieve this goal, a Remote Configuration product may register a PubSub instance. A PubSub class contains a publisher
that receives the Remote Configuration payload and shares it with Pubsub Subscriber instance. The Subscriber starts a
thread on each child process, waiting for a new update of the shared data between the Publisher on the main process
and the child process. Remote Configuration creates a thread listening to the main process for each instance of PubSub.
To connect this publisher and the child processes subscribers, we need a connector class: Shared Memory or File.
Each instance of PubSub works as a singleton when Remote Configuration dispatches the callbacks. That means if we
register the same instance of PubSub class on different products, we would have one thread waiting to the main process.

Each DD Product (APM, ASM, DI, CI) may implement its PubSub Class.

Example 1: A callback for one or more Remote Configuration Products
-------------------------------------------------------------------
AppSec needs to aggregate different products in the same callback for all child processes.

class AppSecRC(PubSubMergeFirst):
    __shared_data__ = ConnectorSharedMemory()

    def __init__(self, _preprocess_results, callback, name="Default"):
        self._publisher = self.__publisher_class__(self.__shared_data__, _preprocess_results)
        self._subscriber = self.__subscriber_class__(self.__shared_data__, callback, name)

asm_callback = AppSecRC(preprocess_1click_activation, appsec_callback, "ASM")

remoteconfig_poller.register("ASM_PRODUCT", asm_callback)
remoteconfig_poller.register("ASM_FEATURES_PRODUCT", asm_callback)


Example 2: One Callback for each product
----------------------------------------
DI needs to aggregate different products in the same callback for all child processes.

class DynamicInstrumentationRC(PubSub):
    __shared_data__ = ConnectorSharedMemory()

    def __init__(self, _preprocess_results, callback, name="Default"):
        self._publisher = self.__publisher_class__(self.__shared_data__, _preprocess_results)
        self._subscriber = self.__subscriber_class__(self.__shared_data__, callback, name)

di_callback_1 = DynamicInstrumentationRC(callback=di_callback_1, name="ASM")
di_callback_2 = DynamicInstrumentationRC(callback=di_callback_2, name="ASM")

remoteconfig_poller.register("DI_1_PRODUCT", di_callback)
remoteconfig_poller.register("DI_2_PRODUCT", di_callback_2)

"""

from abc import ABC
from abc import abstractmethod
from typing import TYPE_CHECKING
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig import ConfigMetadata
from ddtrace.internal.remoteconfig import PayloadType
from ddtrace.internal.remoteconfig._publishers import RemoteConfigPublisherBase
from ddtrace.internal.remoteconfig._subscribers import RemoteConfigSubscriber


if TYPE_CHECKING:
    from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector


log = get_logger(__name__)


class PubSub(ABC):
    _shared_data: Optional["PublisherSubscriberConnector"] = None
    _publisher: Optional[RemoteConfigPublisherBase] = None
    _subscriber: Optional[RemoteConfigSubscriber] = None

    @abstractmethod
    def __init__(self, *args, **kwargs) -> None:
        pass

    def start_subscriber(self):
        if self._subscriber is None:
            log.warning("Subscriber is not initialized")
            return
        self._subscriber.start()

    def restart_subscriber(self, join=False):
        if self._subscriber is None:
            log.warning("Subscriber is not initialized")
            return
        self._subscriber.force_restart(join)

    def _poll_data(self) -> None:
        if self._subscriber is None:
            log.warning("Subscriber is not initialized")
            return
        self._subscriber._get_data_from_connector_and_exec()

    def stop(self, join: bool = False) -> None:
        if self._subscriber is None:
            log.warning("Subscriber is not initialized")
            return
        self._subscriber.stop(join=join)

    def publish(self) -> None:
        if self._publisher is None:
            log.warning("Publisher is not initialized")
            return
        self._publisher.dispatch(self)

    def append_and_publish(self, config_content: PayloadType, target: str, config_metadata: ConfigMetadata) -> None:
        """Append data to publisher and send the data to subscriber. It's a shortcut for testing purposes"""
        self.append(config_content, target, config_metadata)
        self.publish()

    def append(self, config_content: PayloadType, target: str, config_metadata: ConfigMetadata) -> None:
        if self._publisher is None:
            log.warning("Publisher is not initialized")
            return
        self._publisher.append(config_content, target, config_metadata)

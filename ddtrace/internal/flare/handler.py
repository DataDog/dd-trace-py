from ddtrace.internal.flare.flare import Flare


def _tracerFlarePubSub():
    from ddtrace.internal.flare._subscribers import TracerFlareSubscriber
    from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector
    from ddtrace.internal.remoteconfig._publishers import RemoteConfigPublisher
    from ddtrace.internal.remoteconfig._pubsub import PubSub

    class _TracerFlarePubSub(PubSub):
        __publisher_class__ = RemoteConfigPublisher
        __subscriber_class__ = TracerFlareSubscriber
        __shared_data__ = PublisherSubscriberConnector()

        def __init__(self, flare: Flare):
            self._publisher = self.__publisher_class__(self.__shared_data__, None)
            self._subscriber = self.__subscriber_class__(self.__shared_data__, flare)

    return _TracerFlarePubSub

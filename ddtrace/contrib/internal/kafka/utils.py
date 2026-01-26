from time import time

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def _get_cluster_id(instance, topic):
    # Check success cache
    if instance and getattr(instance, "_dd_cluster_id", None):
        return instance._dd_cluster_id

    # Check failure cache - skip for 5 minutes if we fail
    last_failure = getattr(instance, "_dd_cluster_id_failure_time", 0)
    if time() - last_failure < 300:
        return None

    if getattr(instance, "list_topics", None) is None:
        return None

    try:
        cluster_metadata = instance.list_topics(topic=topic, timeout=1.0)
        if cluster_metadata and getattr(cluster_metadata, "cluster_id", None):
            instance._dd_cluster_id = cluster_metadata.cluster_id
            return cluster_metadata.cluster_id
    except Exception:
        # Cache the failure time to avoid repeated slow calls
        instance._dd_cluster_id_failure_time = time()
        log.debug("Failed to get Kafka cluster ID, will retry after 5 minutes")

    return None

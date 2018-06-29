from collections import namedtuple

TAG_NAMES = [
    # OpenTracing tags
    'COMPONENT',
    'DB_INSTANCE',
    'DB_STATEMENT',
    'DB_TYPE',
    'DB_USER',
    'ERROR',
    'HTTP_METHOD',
    'HTTP_STATUS_CODE',
    'HTTP_URL',
    'MSG_BUS_DEST',
    'PEER_ADDRESS',
    'PEER_HOSTNAME',
    'PEER_IPV4',
    'PEER_IPV6',
    'PEER_PORT',
    'PEER_SERVICE',
    'SAMPLING_PRIORITY',
    'SPAN_KIND',
    # Datadog tags
    'RESOURCE_NAME',
    'SERVICE_NAME',
    'SPAN_TYPE',
    'TARGET_HOST',
    'TARGET_PORT',
]

TagNames = namedtuple('TagNames', TAG_NAMES)

Tags = TagNames(
    # OpenTracing tags
    COMPONENT='component',
    DB_INSTANCE='db.instance',
    DB_STATEMENT='db.statement',
    DB_TYPE='db.type',
    DB_USER='db.user',
    ERROR='error',
    HTTP_METHOD='http.method',
    HTTP_STATUS_CODE='http.status_code',
    HTTP_URL='http.url',
    MSG_BUS_DEST='message_bus.destination',
    PEER_ADDRESS='peer.address',
    PEER_HOSTNAME='peer.hostname',
    PEER_IPV4='peer.ipv4',
    PEER_IPV6='peer.ipv6',
    PEER_PORT='peer.port',
    PEER_SERVICE='peer.service',
    SAMPLING_PRIORITY='sampling.priority',
    SPAN_KIND='span.kind',
    # Datadog tags
    RESOURCE_NAME='resource.name',
    SERVICE_NAME='service.name',
    TARGET_HOST='out.host',
    TARGET_PORT='out.port',
    SPAN_TYPE='span.type',
)

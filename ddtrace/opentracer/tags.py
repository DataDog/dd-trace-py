from collections import namedtuple

OT_TAG_NAMES = [
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
    'SPAN_TYPE',
]

OTTagNames = namedtuple('OTTagNames', OT_TAG_NAMES)

OTTags = OTTagNames(
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
    SPAN_TYPE='span.kind',
)

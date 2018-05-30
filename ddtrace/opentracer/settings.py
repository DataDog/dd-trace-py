from collections import namedtuple


CONFIG_KEY_NAMES = [
    'AGENT_HOSTNAME',
    'AGENT_PORT',
    'DEBUG',
    'ENABLED',
    'GLOBAL_TAGS',
    'SERVICE_NAME',
    'CONTEXT_PROVIDER',
    'SAMPLER',
    'PRIORITY_SAMPLING',
    'APP_TYPE',
    'SETTINGS',
]

# Keys used for the configuration dict
ConfigKeyNames = namedtuple('ConfigKeyNames', CONFIG_KEY_NAMES)

ConfigKeys = ConfigKeyNames(
    AGENT_HOSTNAME='agent_hostname',
    AGENT_PORT='agent_port',
    DEBUG='debug',
    ENABLED='enabled',
    GLOBAL_TAGS='global_tags',
    SERVICE_NAME='service_name',
    CONTEXT_PROVIDER='context_provider',
    SAMPLER='sampler',
    PRIORITY_SAMPLING='priority_sampling',
    APP_TYPE='app_type',
    SETTINGS='settings',
)


def config_invalid_keys(config):
    """Returns a list of keys that exist in *config* and not in KEYS."""
    return [key for key in config.keys() if key not in ConfigKeys]

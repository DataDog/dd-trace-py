from ddtrace import config


def _distributed_tracing(self):
    """Backward compatibility"""
    return config.get_from(self)['distributed_tracing']


def _distributed_tracing_setter(self, value):
    """Backward compatibility"""
    config.get_from(self)['distributed_tracing'] = value

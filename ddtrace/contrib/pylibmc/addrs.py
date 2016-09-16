

translate_server_specs = None

try:
    from pylibmc.client import translate_server_specs
except ImportError:
    pass

def parse_addresses(addrs):
    if not translate_server_specs:
        return []
    return translate_server_specs(addrs)

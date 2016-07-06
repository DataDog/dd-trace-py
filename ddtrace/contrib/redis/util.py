"""
Some utils used by the dogtrace redis integration
"""
from ...compat import stringify
from ...ext import redis as redisx, net

VALUE_PLACEHOLDER = "?"
VALUE_MAX_LENGTH = 100
VALUE_TOO_LONG_MARK = "..."
COMMAND_MAX_LENGTH = 1000


def _extract_conn_tags(conn_kwargs):
    """ Transform redis conn info into dogtrace metas """
    try:
        return {
            net.TARGET_HOST: conn_kwargs['host'],
            net.TARGET_PORT: conn_kwargs['port'],
            redisx.DB: conn_kwargs['db'] or 0,
        }
    except Exception:
        return {}


def format_command_args(args):
    """Format a command by removing unwanted values

    Restrict what we keep from the values sent (with a SET, HGET, LPUSH, ...):
      - Skip binary content
      - Truncate
    """
    formatted_length = 0
    formatted_args = []
    for arg in args:
        try:
            command = stringify(arg)
            if len(command) > VALUE_MAX_LENGTH:
                command = command[:VALUE_MAX_LENGTH] + VALUE_TOO_LONG_MARK
            if formatted_length + len(command) > COMMAND_MAX_LENGTH:
                formatted_args.append(
                    command[:COMMAND_MAX_LENGTH-formatted_length]
                    + VALUE_TOO_LONG_MARK
                )
                break

            formatted_args.append(command)
            formatted_length += len(command)
        except Exception:
            formatted_args.append(VALUE_PLACEHOLDER)
            break

    return " ".join(formatted_args)

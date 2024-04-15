import os


def get_default_telemetry_env(update_with=None, agentless=False):
    env = os.environ.copy()

    if update_with:
        for k, v in update_with.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v

    # The default environment for the telemetry writer tests disables agentless mode because the behavior is identical
    # except for the trace URL, endpoint, and presence of an API key header.
    env["DD_CIVISIBILITY_AGENTLESS_ENABLED"] = "true" if agentless else "false"

    return env

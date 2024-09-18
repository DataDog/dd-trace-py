import os.path


ROOT_DIR = os.path.dirname(os.path.abspath(__file__)) + "/appsec"
RULES_GOOD_PATH = os.path.join(ROOT_DIR, "rules-good.json")
RULES_BAD_PATH = os.path.join(ROOT_DIR, "rules-bad.json")
RULES_MISSING_PATH = os.path.join(ROOT_DIR, "nonexistent")
RULES_SAB = os.path.join(ROOT_DIR, "rules-suspicious-attacker-blocking.json")
RULES_SRB = os.path.join(ROOT_DIR, "rules-suspicious-requests.json")
RULES_SRBCA = os.path.join(ROOT_DIR, "rules-suspicious-requests-custom-actions.json")
RULES_SRB_RESPONSE = os.path.join(ROOT_DIR, "rules-suspicious-requests-response.json")
RULES_SRB_METHOD = os.path.join(ROOT_DIR, "rules-suspicious-requests-get.json")
RULES_BAD_VERSION = os.path.join(ROOT_DIR, "rules-bad_version.json")
RULES_EXPLOIT_PREVENTION = os.path.join(ROOT_DIR, "rules-rasp.json")
RULES_EXPLOIT_PREVENTION_BLOCKING = os.path.join(ROOT_DIR, "rules-rasp-blocking.json")
RULES_EXPLOIT_PREVENTION_REDIRECTING = os.path.join(ROOT_DIR, "rules-rasp-redirecting.json")
RULES_EXPLOIT_PREVENTION_DISABLED = os.path.join(ROOT_DIR, "rules-rasp-disabled.json")

RESPONSE_CUSTOM_JSON = os.path.join(ROOT_DIR, "response-custom.json")
RESPONSE_CUSTOM_HTML = os.path.join(ROOT_DIR, "response-custom.html")


class _IP:
    BLOCKED = "8.8.4.4"  # actively blocked
    MONITORED = "8.8.5.5"  # on the pass list should never been blocked but still monitored
    BYPASS = "8.8.6.6"  # on the pass list, should bypass all security
    DEFAULT = "1.1.1.1"  # default behaviour


class Config(object):
    def __init__(self):
        self.is_header_tracing_configured = False

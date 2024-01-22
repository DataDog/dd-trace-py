import os.path


ROOT_DIR = os.path.dirname(os.path.abspath(__file__)) + "/appsec"
RULES_GOOD_PATH = os.path.join(ROOT_DIR, "rules-good.json")
RULES_BAD_PATH = os.path.join(ROOT_DIR, "rules-bad.json")
RULES_MISSING_PATH = os.path.join(ROOT_DIR, "nonexistent")
RULES_SRB = os.path.join(ROOT_DIR, "rules-suspicious-requests.json")
RULES_SRBCA = os.path.join(ROOT_DIR, "rules-suspicious-requests-custom-actions.json")
RULES_SRB_RESPONSE = os.path.join(ROOT_DIR, "rules-suspicious-requests-response.json")
RULES_SRB_METHOD = os.path.join(ROOT_DIR, "rules-suspicious-requests-get.json")
RULES_BAD_VERSION = os.path.join(ROOT_DIR, "rules-bad_version.json")

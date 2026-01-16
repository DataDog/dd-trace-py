"""
Hardcoded secret detection rules for IAST.

This module contains regex patterns to detect various types of hardcoded secrets in Python code.
The rules are based on dd-trace-js implementation and adapted for Python.
"""

from enum import Enum
import re
from typing import List
from typing import NamedTuple


class RuleType(Enum):
    """Type of hardcoded secret rule."""

    VALUE_ONLY = "value_only"  # Matches based on value pattern only
    NAME_AND_VALUE = "name_and_value"  # Matches based on variable name + value pattern


class SecretRule(NamedTuple):
    """A rule for detecting hardcoded secrets."""

    id: str
    regex: re.Pattern
    type: RuleType


# Hardcoded secret detection rules
# These are compiled regex patterns to detect various secret types
HARDCODED_SECRET_RULES: List[SecretRule] = [
    # AWS Access Keys
    SecretRule(
        id="aws-access-token",
        regex=re.compile(r"\b((A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16})(?:['\"\s``;]|$)"),
        type=RuleType.VALUE_ONLY,
    ),
    # GitHub Tokens
    SecretRule(
        id="github-pat",
        regex=re.compile(r"ghp_[0-9a-zA-Z]{36}"),
        type=RuleType.VALUE_ONLY,
    ),
    SecretRule(
        id="github-oauth",
        regex=re.compile(r"gho_[0-9a-zA-Z]{36}"),
        type=RuleType.VALUE_ONLY,
    ),
    SecretRule(
        id="github-app-token",
        regex=re.compile(r"(ghu|ghs)_[0-9a-zA-Z]{36}"),
        type=RuleType.VALUE_ONLY,
    ),
    SecretRule(
        id="github-fine-grained-pat",
        regex=re.compile(r"github_pat_[0-9a-zA-Z_]{82}"),
        type=RuleType.VALUE_ONLY,
    ),
    # GitLab Tokens
    SecretRule(
        id="gitlab-pat",
        regex=re.compile(r"glpat-[0-9a-zA-Z\-_]{20}"),
        type=RuleType.VALUE_ONLY,
    ),
    SecretRule(
        id="gitlab-ptt",
        regex=re.compile(r"glptt-[0-9a-f]{40}"),
        type=RuleType.VALUE_ONLY,
    ),
    SecretRule(
        id="gitlab-rrt",
        regex=re.compile(r"GR1348941[0-9a-zA-Z\-_]{20}"),
        type=RuleType.VALUE_ONLY,
    ),
    # GCP API Key
    SecretRule(
        id="gcp-api-key",
        regex=re.compile(r"\b(AIza[0-9a-z\-_]{35})(?:['\"\s``;]|$)", re.IGNORECASE),
        type=RuleType.VALUE_ONLY,
    ),
    # Slack Tokens
    SecretRule(
        id="slack-bot-token",
        regex=re.compile(r"(xoxb-[0-9]{10,13}-[0-9]{10,13}[a-zA-Z0-9-]*)"),
        type=RuleType.VALUE_ONLY,
    ),
    SecretRule(
        id="slack-user-token",
        regex=re.compile(r"(xox[pe](?:-[0-9]{10,13}){3}-[a-zA-Z0-9-]{28,34})"),
        type=RuleType.VALUE_ONLY,
    ),
    SecretRule(
        id="slack-webhook-url",
        regex=re.compile(r"(https?://)?hooks\.slack\.com/(services|workflows)/[A-Za-z0-9+/]{43,46}"),
        type=RuleType.VALUE_ONLY,
    ),
    # Stripe Keys
    SecretRule(
        id="stripe-access-token",
        regex=re.compile(r"(sk|pk)_(test|live)_[0-9a-z]{10,32}", re.IGNORECASE),
        type=RuleType.VALUE_ONLY,
    ),
    # NPM Token
    SecretRule(
        id="npm-access-token",
        regex=re.compile(r"\b(npm_[a-z0-9]{36})(?:['\"\s``;]|$)", re.IGNORECASE),
        type=RuleType.VALUE_ONLY,
    ),
    # PyPI Token
    SecretRule(
        id="pypi-upload-token",
        regex=re.compile(r"pypi-AgEIcHlwaS5vcmc[A-Za-z0-9\-_]{50,1000}"),
        type=RuleType.VALUE_ONLY,
    ),
    # SendGrid API Token
    SecretRule(
        id="sendgrid-api-token",
        regex=re.compile(r"\b(SG\.[a-z0-9=_\-.]{66})(?:['\"\s``;]|$)", re.IGNORECASE),
        type=RuleType.VALUE_ONLY,
    ),
    # Twilio API Key
    SecretRule(
        id="twilio-api-key",
        regex=re.compile(r"SK[0-9a-fA-F]{32}"),
        type=RuleType.VALUE_ONLY,
    ),
    # JWT Tokens
    SecretRule(
        id="jwt",
        regex=re.compile(
            r"\b(ey[a-zA-Z0-9]{17,}\.ey[a-zA-Z0-9/_-]{17,}\.(?:[a-zA-Z0-9/_-]{10,}={0,2})?)(?:['\"\s``;]|$)"
        ),
        type=RuleType.VALUE_ONLY,
    ),
    # Private Keys
    SecretRule(
        id="private-key",
        regex=re.compile(
            r"-----BEGIN[ A-Z0-9_-]{0,100}PRIVATE KEY( BLOCK)?-----[\s\S]*KEY( BLOCK)?----", re.IGNORECASE
        ),
        type=RuleType.VALUE_ONLY,
    ),
    # Datadog API Token (NAME_AND_VALUE type)
    SecretRule(
        id="datadog-access-token",
        regex=re.compile(
            r"(?:datadog)(?:[0-9a-z\-_\t.]{0,20})(?:[\s|']|[\s|\"\"]){0,3}"
            r"(?:=|>|:{1,3}=|\|\|:|<=|=>|:|\?=)(?:'|\"\"|\s|=|`){0,5}"
            r"([a-z0-9]{40})(?:['\"\s``;]|$)",
            re.IGNORECASE,
        ),
        type=RuleType.NAME_AND_VALUE,
    ),
    # Generic API key patterns (NAME_AND_VALUE type)
    SecretRule(
        id="generic-api-key",
        regex=re.compile(
            r"(?:api[_-]?key|apikey)(?:[0-9a-z\-_\t.]{0,20})(?:[\s|']|[\s|\"\"]){0,3}"
            r"(?:=|>|:{1,3}=|\|\|:|<=|=>|:|\?=)(?:'|\"\"|\s|=|`){0,5}"
            r"(['\"][a-z0-9]{16,}['\"])(?:['\"\s``;]|$)",
            re.IGNORECASE,
        ),
        type=RuleType.NAME_AND_VALUE,
    ),
    # OpenAI API Key
    SecretRule(
        id="openai-api-key",
        regex=re.compile(r"\b(sk-[a-z0-9]{20}T3BlbkFJ[a-z0-9]{20})(?:['\"\s``;]|$)", re.IGNORECASE),
        type=RuleType.VALUE_ONLY,
    ),
    # DigitalOcean Tokens
    SecretRule(
        id="digitalocean-pat",
        regex=re.compile(r"\b(dop_v1_[a-f0-9]{64})(?:['\"\s``;]|$)", re.IGNORECASE),
        type=RuleType.VALUE_ONLY,
    ),
    SecretRule(
        id="digitalocean-access-token",
        regex=re.compile(r"\b(doo_v1_[a-f0-9]{64})(?:['\"\s``;]|$)", re.IGNORECASE),
        type=RuleType.VALUE_ONLY,
    ),
    # Databricks Token
    SecretRule(
        id="databricks-api-token",
        regex=re.compile(r"\b(dapi[a-h0-9]{32})(?:['\"\s``;]|$)", re.IGNORECASE),
        type=RuleType.VALUE_ONLY,
    ),
    # Heroku API Key
    SecretRule(
        id="heroku-api-key",
        regex=re.compile(
            r"(?:heroku)(?:[0-9a-z\-_\t.]{0,20})(?:[\s|']|[\s|\"\"]){0,3}"
            r"(?:=|>|:{1,3}=|\|\|:|<=|=>|:|\?=)(?:'|\"\"|\s|=|`){0,5}"
            r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:['\"\s``;]|$)",
            re.IGNORECASE,
        ),
        type=RuleType.NAME_AND_VALUE,
    ),
    # Square Tokens
    SecretRule(
        id="square-access-token",
        regex=re.compile(r"\b(sq0atp-[0-9a-z\-_]{22})(?:['\"\s``;]|$)", re.IGNORECASE),
        type=RuleType.VALUE_ONLY,
    ),
    SecretRule(
        id="square-secret",
        regex=re.compile(r"\b(sq0csp-[0-9a-z\-_]{43})(?:['\"\s``;]|$)", re.IGNORECASE),
        type=RuleType.VALUE_ONLY,
    ),
]


# Separate rules by type for efficient matching
VALUE_ONLY_RULES = [rule for rule in HARDCODED_SECRET_RULES if rule.type == RuleType.VALUE_ONLY]
NAME_AND_VALUE_RULES = [rule for rule in HARDCODED_SECRET_RULES if rule.type == RuleType.NAME_AND_VALUE]

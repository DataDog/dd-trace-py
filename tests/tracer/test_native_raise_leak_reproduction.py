from ddtrace.internal.sampling import get_span_sampling_rules
from ddtrace.internal.settings._config import Config

from ..utils import override_env
from ..utils import override_global_config


def _unsupported_rule_is_rejected():
    with override_global_config(dict(_sampling_rules='[{"service":"h[!a]i"}]')):
        assert get_span_sampling_rules() == []


def test_control_unsupported_rule_is_rejected():
    _unsupported_rule_is_rejected()


def test_reproduction_config_construction_does_not_disable_validation():
    with override_env({}, replace_os_env=True):
        Config()

    _unsupported_rule_is_rejected()

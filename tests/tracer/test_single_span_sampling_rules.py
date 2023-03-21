import sys

from jsonschema import ValidationError
import pytest

from ddtrace import Tracer
from ddtrace.constants import SAMPLING_PRIORITY_KEY
from ddtrace.constants import _SINGLE_SPAN_SAMPLING_MAX_PER_SEC
from ddtrace.constants import _SINGLE_SPAN_SAMPLING_MECHANISM
from ddtrace.constants import _SINGLE_SPAN_SAMPLING_RATE
from ddtrace.internal.sampling import SamplingMechanism
from ddtrace.internal.sampling import _get_file_json
from ddtrace.internal.sampling import get_span_sampling_rules
from tests.utils import DummyWriter

from ..utils import override_env


def test_sampling_rule_init_via_env():
    # Testing single sampling rule
    with override_env(
        dict(DD_SPAN_SAMPLING_RULES='[{"sample_rate":0.5,"service":"xyz","name":"abc","max_per_second":100}]')
    ):
        sampling_rules = get_span_sampling_rules()
        assert sampling_rules[0]._sample_rate == 0.5
        assert sampling_rules[0]._service_matcher.pattern == "xyz"
        assert sampling_rules[0]._name_matcher.pattern == "abc"
        assert sampling_rules[0]._max_per_second == 100
        assert len(sampling_rules) == 1

    # Testing multiple sampling rules
    with override_env(
        dict(
            DD_SPAN_SAMPLING_RULES='[{"service":"xy?","name":"a*c"}, \
            {"sample_rate":0.5,"service":"my-service","name":"my-name", "max_per_second":20}]'
        )
    ):
        sampling_rules = get_span_sampling_rules()
        assert sampling_rules[0]._sample_rate == 1.0
        assert sampling_rules[0]._service_matcher.pattern == "xy?"
        assert sampling_rules[0]._name_matcher.pattern == "a*c"
        assert sampling_rules[0]._max_per_second == -1

        assert sampling_rules[1]._sample_rate == 0.5
        assert sampling_rules[1]._service_matcher.pattern == "my-service"
        assert sampling_rules[1]._name_matcher.pattern == "my-name"
        assert sampling_rules[1]._max_per_second == 20
        assert len(sampling_rules) == 2

    # Testing for only service being set
    with override_env(dict(DD_SPAN_SAMPLING_RULES='[{"service":"xyz", "sample_rate":0.23}]')):
        sampling_rules = get_span_sampling_rules()
        assert sampling_rules[0]._sample_rate == 0.23
        assert sampling_rules[0]._service_matcher.pattern == "xyz"
        assert sampling_rules[0]._max_per_second == -1
        assert len(sampling_rules) == 1

    # Testing for only name being set
    with override_env(dict(DD_SPAN_SAMPLING_RULES='[{"name":"xyz"}]')):
        sampling_rules = get_span_sampling_rules()
        assert sampling_rules[0]._sample_rate == 1.0
        assert sampling_rules[0]._name_matcher.pattern == "xyz"
        assert sampling_rules[0]._max_per_second == -1
        assert len(sampling_rules) == 1

    # Testing error thrown when neither name nor service is set
    with override_env(dict(DD_SPAN_SAMPLING_RULES='[{"sample_rate":1.0}]')):
        with pytest.raises(ValidationError):
            sampling_rules = get_span_sampling_rules()

    # Testing exception thrown when service pattern contains unsupported char
    with override_env(dict(DD_SPAN_SAMPLING_RULES='[{"service":"h[!a]i"}]')):
        with pytest.raises(ValueError):
            sampling_rules = get_span_sampling_rules()

    # Testing exception thrown when name pattern contains unsupported char
    with override_env(dict(DD_SPAN_SAMPLING_RULES='[{"name":"h[!a]i"}]')):
        with pytest.raises(ValueError):
            sampling_rules = get_span_sampling_rules()


def test_json_not_list_error():
    with override_env(
        dict(DD_SPAN_SAMPLING_RULES='{"sample_rate":0.5,"service":"xyz","name":"abc","max_per_second":100}')
    ):
        with pytest.raises(TypeError):
            get_span_sampling_rules()


def test_json_decode_error_throws_ValueError():
    with override_env(
        dict(DD_SPAN_SAMPLING_RULES='{"sample_rate":0.5,"service":"xyz","name":"abc","max_per_second":100')
    ):
        with pytest.raises(ValueError):
            get_span_sampling_rules()


def test_rules_sample_span_via_env():
    """Test that single span sampling tags are applied to spans that should get sampled when envars set"""
    with override_env(dict(DD_SPAN_SAMPLING_RULES='[{"service":"test_service","name":"test_name"}]')):
        sampling_rules = get_span_sampling_rules()
        assert sampling_rules[0]._service_matcher.pattern == "test_service"
        assert sampling_rules[0]._name_matcher.pattern == "test_name"
        tracer = Tracer()
        tracer.configure(writer=DummyWriter())

        span = traced_function(tracer)

        assert_sampling_decision_tags(span)


def test_rules_do_not_sample_wrong_span_via_env():
    """Test that single span sampling tags are not applied to spans that do not match rules"""
    with override_env(dict(DD_SPAN_SAMPLING_RULES='[{"service":"test_ser","name":"test_na"}]')):
        sampling_rules = get_span_sampling_rules()
        assert sampling_rules[0]._service_matcher.pattern == "test_ser"
        assert sampling_rules[0]._name_matcher.pattern == "test_na"
        tracer = Tracer()
        tracer.configure(writer=DummyWriter())

        span = traced_function(tracer)

        assert_sampling_decision_tags(span, sample_rate=None, mechanism=None, limit=None)


def test_single_span_rules_do_not_tag_if_tracer_samples_via_env():
    """Test that single span sampling rules aren't applied if a span is already going to be sampled by trace sampler"""
    with override_env(dict(DD_SPAN_SAMPLING_RULES='[{"service":"test_service","name":"test_name"}]')):
        sampling_rules = get_span_sampling_rules()
        assert sampling_rules[0]._service_matcher.pattern == "test_service"
        assert sampling_rules[0]._name_matcher.pattern == "test_name"
        tracer = Tracer()
        tracer.configure(writer=DummyWriter())

        span = traced_function(tracer, trace_sampling=True)

        assert sampling_rules[0].match(span) is True

        assert_sampling_decision_tags(span, sample_rate=None, mechanism=None, limit=None, trace_sampling=True)


def test_sampling_rule_init_config_multiple_sampling_rule_json_via_file(tmpdir):
    file = tmpdir.join("rules.json")
    file.write(
        '[{"service":"xy?","name":"a*c"}, \
            {"sample_rate":0.5,"service":"my-service","name":"my-name", "max_per_second":"20"}]'
    )

    with override_env(dict(DD_SPAN_SAMPLING_RULES_FILE=str(file))):
        sampling_rules = _get_file_json()
        assert sampling_rules == [
            {"service": "xy?", "name": "a*c"},
            {"sample_rate": 0.5, "service": "my-service", "name": "my-name", "max_per_second": "20"},
        ]


def test_wrong_file_path(tmpdir):
    """Test that single span sampling tags are not applied to spans that do not match rules via file"""
    with override_env(dict(DD_SPAN_SAMPLING_RULES_FILE="data/this_doesnt_exist.json")):
        exception = FileNotFoundError if sys.version_info.major > 3 else IOError
        with pytest.raises(exception):
            get_span_sampling_rules()


def test_default_to_env_if_both_env_and_file_config(tmpdir, caplog):
    file = tmpdir.join("rules.json")
    file.write('[{"sample_rate":1.0,"service":"x","name":"ab","max_per_second":1000}]')

    with override_env(
        dict(
            DD_SPAN_SAMPLING_RULES_FILE=str(file),
            DD_SPAN_SAMPLING_RULES='[{"sample_rate":0.5,"service":"xyz","name":"abc","max_per_second":100}]',
        )
    ):
        sampling_rules = get_span_sampling_rules()
        assert caplog.record_tuples == [
            (
                "ddtrace.internal.sampling",
                30,
                "DD_SPAN_SAMPLING_RULES and DD_SPAN_SAMPLING_RULES_FILE detected. "
                "Defaulting to DD_SPAN_SAMPLING_RULES value.",
            )
        ]
        assert sampling_rules[0]._sample_rate == 0.5
        assert sampling_rules[0]._service_matcher.pattern == "xyz"
        assert sampling_rules[0]._name_matcher.pattern == "abc"
        assert sampling_rules[0]._max_per_second == 100
        assert len(sampling_rules) == 1


def traced_function(tracer, name="test_name", service="test_service", trace_sampling=False):
    with tracer.trace(name) as span:
        # If the trace sampler samples the trace, then we shouldn't add the span sampling tags
        if trace_sampling:
            span.context.sampling_priority = 1
        else:
            span.context.sampling_priority = 0

        span.service = service
    return span


def assert_sampling_decision_tags(
    span, sample_rate=1.0, mechanism=SamplingMechanism.SPAN_SAMPLING_RULE, limit=None, trace_sampling=False
):
    assert span.get_metric(_SINGLE_SPAN_SAMPLING_RATE) == sample_rate
    assert span.get_metric(_SINGLE_SPAN_SAMPLING_MECHANISM) == mechanism
    assert span.get_metric(_SINGLE_SPAN_SAMPLING_MAX_PER_SEC) == limit

    if trace_sampling:
        assert span.get_metric(SAMPLING_PRIORITY_KEY) > 0

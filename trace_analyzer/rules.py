from trace_loader import Span

span_rules = []

def span_rule(description=None):
    def add_span_rule(rule):
        span_rules.append((rule.__name__, description, rule))
        return rule
    return add_span_rule

def application_root_span_service_matches_config(span, tracer_configuration):
    description = """
    The service name of the first span for this applications should either be the framework name, if the framework is a server, or the application name.
    """
    base_application_name = tracer_configuration["base_application_root_service"]
    span_service = span["service"]

    valid = False
    reason = "N/A"
    if base_application_name != span_service:
        reason = "Root span service is not the base service"
    else:
        valid = True

    print("- Comparing base application root span service with expected service. Actual: %r, Expected: %r. Evaluation result: %s, Reason: %s" % (base_application_name, span_service, valid, reason))

    return valid

@span_rule(description="All spans should have a service name")
def all_spans_have_a_service(span: Span):
    valid = hasattr(span, 'service') and span.service not in (None, "")

    print("Span rule ran on span %s: All spans should have a service name. Evaluation result: %s" % (span.id, valid))


def apply_rules():
    print("Applying rules")
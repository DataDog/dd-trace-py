import sys

from ddtrace.appsec._iast import _is_iast_enabled
from ddtrace.contrib.internal.langchain.utils import PATCH_LANGCHAIN_V0
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import deep_getattr


def taint_outputs(instance, inputs, outputs):
    from ddtrace.appsec._iast._metrics import _set_iast_error_metric
    from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges
    from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject

    try:
        ranges = None
        for key in filter(lambda x: x in inputs, instance.input_keys):
            input_val = inputs.get(key)
            if input_val:
                ranges = get_tainted_ranges(input_val)
                if ranges:
                    break

        if ranges:
            source = ranges[0].source
            for key in filter(lambda x: x in outputs, instance.output_keys):
                output_value = outputs[key]
                outputs[key] = taint_pyobject(output_value, source.name, source.value, source.origin)
    except Exception as e:
        _set_iast_error_metric("IAST propagation error. langchain taint_outputs. {}".format(e))


@with_traced_module
def traced_chain_call(langchain, pin, func, instance, args, kwargs):
    integration = langchain._datadog_integration
    span = integration.trace(
        pin,
        "{}.{}".format(instance.__module__, instance.__class__.__name__),
        submit_to_llmobs=True,
        interface_type="chain",
    )
    inputs = None
    final_outputs = {}
    try:
        if PATCH_LANGCHAIN_V0:
            inputs = get_argument_value(args, kwargs, 0, "inputs")
        else:
            inputs = get_argument_value(args, kwargs, 0, "input")
        if not isinstance(inputs, dict):
            inputs = {instance.input_keys[0]: inputs}
        if integration.is_pc_sampled_span(span):
            for k, v in inputs.items():
                span.set_tag_str("langchain.request.inputs.%s" % k, integration.trunc(str(v)))
            template = deep_getattr(instance, "prompt.template", default="")
            if template:
                span.set_tag_str("langchain.request.prompt", integration.trunc(str(template)))
        final_outputs = func(*args, **kwargs)
        if integration.is_pc_sampled_span(span):
            for k, v in final_outputs.items():
                span.set_tag_str("langchain.response.outputs.%s" % k, integration.trunc(str(v)))
        if _is_iast_enabled():
            taint_outputs(instance, inputs, final_outputs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.metric(span, "incr", "request.error", 1)
        raise
    finally:
        integration.llmobs_set_tags(span, args=[], kwargs=inputs, response=final_outputs, operation="chain")
        span.finish()
        integration.metric(span, "dist", "request.duration", span.duration_ns)
        if integration.is_pc_sampled_log(span):
            log_inputs = {}
            log_outputs = {}
            for k, v in inputs.items():
                log_inputs[k] = str(v)
            for k, v in final_outputs.items():
                log_outputs[k] = str(v)
            integration.log(
                span,
                "info" if span.error == 0 else "error",
                "sampled %s.%s" % (instance.__module__, instance.__class__.__name__),
                attrs={
                    "inputs": log_inputs,
                    "prompt": str(deep_getattr(instance, "prompt.template", default="")),
                    "outputs": log_outputs,
                },
            )
    return final_outputs


@with_traced_module
async def traced_chain_acall(langchain, pin, func, instance, args, kwargs):
    integration = langchain._datadog_integration
    span = integration.trace(
        pin,
        "{}.{}".format(instance.__module__, instance.__class__.__name__),
        submit_to_llmobs=True,
        interface_type="chain",
    )
    inputs = None
    final_outputs = {}
    try:
        if PATCH_LANGCHAIN_V0:
            inputs = get_argument_value(args, kwargs, 0, "inputs")
        else:
            inputs = get_argument_value(args, kwargs, 0, "input")
        if not isinstance(inputs, dict):
            inputs = {instance.input_keys[0]: inputs}
        if integration.is_pc_sampled_span(span):
            for k, v in inputs.items():
                span.set_tag_str("langchain.request.inputs.%s" % k, integration.trunc(str(v)))
            template = deep_getattr(instance, "prompt.template", default="")
            if template:
                span.set_tag_str("langchain.request.prompt", integration.trunc(str(template)))
        final_outputs = await func(*args, **kwargs)
        if integration.is_pc_sampled_span(span):
            for k, v in final_outputs.items():
                span.set_tag_str("langchain.response.outputs.%s" % k, integration.trunc(str(v)))
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.metric(span, "incr", "request.error", 1)
        raise
    finally:
        integration.llmobs_set_tags(span, args=[], kwargs=inputs, response=final_outputs, operation="chain")
        span.finish()
        integration.metric(span, "dist", "request.duration", span.duration_ns)
        if integration.is_pc_sampled_log(span):
            log_inputs = {}
            log_outputs = {}
            for k, v in inputs.items():
                log_inputs[k] = str(v)
            for k, v in final_outputs.items():
                log_outputs[k] = str(v)
            integration.log(
                span,
                "info" if span.error == 0 else "error",
                "sampled %s.%s" % (instance.__module__, instance.__class__.__name__),
                attrs={
                    "inputs": log_inputs,
                    "prompt": str(deep_getattr(instance, "prompt.template", default="")),
                    "outputs": log_outputs,
                },
            )
    return final_outputs


@with_traced_module
def traced_lcel_runnable_sequence(langchain, pin, func, instance, args, kwargs):
    """
    Traces the top level call of a LangChain Expression Language (LCEL) chain.

    LCEL is a new way of chaining in LangChain. It works by piping the output of one step of a chain into the next.
    This is similar in concept to the legacy LLMChain class, but instead relies internally on the idea of a
    RunnableSequence. It uses the operator `|` to create an implicit chain of `Runnable` steps.

    It works with a set of useful tools that distill legacy ways of creating chains,
    and various tasks and tooling within, making it preferable to LLMChain and related classes.

    This method captures the initial inputs to the chain, as well as the final outputs, and tags them appropriately.
    """
    integration = langchain._datadog_integration
    span = integration.trace(
        pin,
        "{}.{}".format(instance.__module__, instance.__class__.__name__),
        submit_to_llmobs=True,
        interface_type="chain",
    )
    inputs = None
    final_output = None
    try:
        try:
            inputs = get_argument_value(args, kwargs, 0, "input")
        except ArgumentError:
            inputs = get_argument_value(args, kwargs, 0, "inputs")
        if integration.is_pc_sampled_span(span):
            if not isinstance(inputs, list):
                inputs = [inputs]
            for idx, inp in enumerate(inputs):
                if not isinstance(inp, dict):
                    span.set_tag_str("langchain.request.inputs.%d" % idx, integration.trunc(str(inp)))
                else:
                    for k, v in inp.items():
                        span.set_tag_str("langchain.request.inputs.%d.%s" % (idx, k), integration.trunc(str(v)))
        final_output = func(*args, **kwargs)
        if integration.is_pc_sampled_span(span):
            final_outputs = final_output  # separate variable as to return correct value later
            if not isinstance(final_outputs, list):
                final_outputs = [final_outputs]
            for idx, output in enumerate(final_outputs):
                span.set_tag_str("langchain.response.outputs.%d" % idx, integration.trunc(str(output)))
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.metric(span, "incr", "request.error", 1)
        raise
    finally:
        integration.llmobs_set_tags(span, args=[], kwargs=inputs, response=final_output, operation="chain")
        span.finish()
        integration.metric(span, "dist", "request.duration", span.duration_ns)
    return final_output


@with_traced_module
async def traced_lcel_runnable_sequence_async(langchain, pin, func, instance, args, kwargs):
    """
    Similar to `traced_lcel_runnable_sequence`, but for async chaining calls.
    """
    integration = langchain._datadog_integration
    span = integration.trace(
        pin,
        "{}.{}".format(instance.__module__, instance.__class__.__name__),
        submit_to_llmobs=True,
        interface_type="chain",
    )
    inputs = None
    final_output = None
    try:
        try:
            inputs = get_argument_value(args, kwargs, 0, "input")
        except ArgumentError:
            inputs = get_argument_value(args, kwargs, 0, "inputs")
        if integration.is_pc_sampled_span(span):
            if not isinstance(inputs, list):
                inputs = [inputs]
            for idx, inp in enumerate(inputs):
                if not isinstance(inp, dict):
                    span.set_tag_str("langchain.request.inputs.%d" % idx, integration.trunc(str(inp)))
                else:
                    for k, v in inp.items():
                        span.set_tag_str("langchain.request.inputs.%d.%s" % (idx, k), integration.trunc(str(v)))
        final_output = await func(*args, **kwargs)
        if integration.is_pc_sampled_span(span):
            final_outputs = final_output  # separate variable as to return correct value later
            if not isinstance(final_outputs, list):
                final_outputs = [final_outputs]
            for idx, output in enumerate(final_outputs):
                span.set_tag_str("langchain.response.outputs.%d" % idx, integration.trunc(str(output)))
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.metric(span, "incr", "request.error", 1)
        raise
    finally:
        integration.llmobs_set_tags(span, args=[], kwargs=inputs, response=final_output, operation="chain")
        span.finish()
        integration.metric(span, "dist", "request.duration", span.duration_ns)
    return final_output

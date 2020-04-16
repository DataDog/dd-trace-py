import logging

if __name__ == "__main__":
    # Ensure if module is patched then default log formatter is set up for logs
    if getattr(logging, "_datadog_patch"):
        assert (
            "[dd.service=%(dd.service)s dd.env=%(dd.env)s dd.version=%(dd.version)s"
            " dd.trace_id=%(dd.trace_id)s dd.span_id=%(dd.span_id)s]"
            in logging.root.handlers[0].formatter._fmt
        )
    else:
        assert (
            "[dd.service=%(dd.service)s dd.env=%(dd.env)s dd.version=%(dd.version)s"
            " dd.trace_id=%(dd.trace_id)s dd.span_id=%(dd.span_id)s]"
            not in logging.root.handlers[0].formatter._fmt
        )
    print("Test success")

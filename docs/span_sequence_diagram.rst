.. _`span_sequence_diagram`:

Span Sequence Diagram 
===========================

.. seqdiag::
  seqdiag {
    application  -> tracer [label = "trace()"];
    tracer  -> context [label = "active()"];
    tracer <-- context [label = "context object"];
    tracer -> span [label = "start_span()"];
    tracer <-- span [label = "span object"];
    application <-- tracer [label = "span object"];
    tracer --> context [label = "active span"];
    tracer -> spanproccessor [label = "on_span_start()"];
    tracer -> hooks [label = "emit on_span_start"];

    // Delay separator
    === Span Created ===

    application -> span [label = "finish()"];
    tracer <-  span [label = "_on_finish_callbacks()"];
    tracer -> spanproccessor [label = "on_span_finsh()"];  
  }
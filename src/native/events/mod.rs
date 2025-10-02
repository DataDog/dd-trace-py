/*!
# Event Hub Module

A high-performance event system for registering and calling listeners from both Python and Rust code.
Provides minimal-overhead event firing with automatic Python/Rust interoperability.

## Usage Examples

```rust
use crate::events::trace_span_started;

// Register a listener
let handle = trace_span_started::on(|span| {
    println!("Span started: {:?}", span);
});

// Fire event (only if listeners exist for performance)
if trace_span_started::has_listeners() {
    trace_span_started::dispatch(span_pyobject);
}

// Remove listener
trace_span_started::remove(handle);
```

```python
from ddtrace.internal.events import trace_span_started

def my_span_handler(span):
    print(f"Span started: {span}")

handle = trace_span_started.on(my_span_handler)
trace_span_started.dispatch(some_span)
trace_span_started.remove(handle)
```
*/

// Implementation details
pub mod macros;

// Re-export public types
pub use macros::events;

// Make macros available in this module scope
#[allow(unused_imports)]
use crate::{define_event, define_native_event};

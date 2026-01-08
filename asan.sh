export ASAN_OPTIONS="detect_leaks=1 \
detect_stack_use_after_return=1 \
detect_invalid_pointer_pairs=2 \
strict_init_order=1 \
strict_string_checks=1 \
alloc_dealloc_mismatch=1 \
check_malloc_usable_size=1 \
detect_container_overflow=1 \
detect_odr_violation=2 \
halt_on_error=1 \
abort_on_error=1 \
fast_unwind_on_malloc=0 \
malloc_context_size=50 \
redzone=2048 \
max_allocation_size_mb=4096 \
detect_stack_use_after_return=1"

export DDTRACE_PROFILING_ENABLED=true

ddtrace-run python3 whatever_async.py

# detect_stack_use_after_scope=1 \
# verbosity=1 \

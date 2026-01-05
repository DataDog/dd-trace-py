#define CHECK_IAST_INITIALIZED_OR_RETURN(fallback_result)                                                              \
    if (!taint_engine_context || !initializer) {                                                                       \
        return fallback_result;                                                                                        \
    }

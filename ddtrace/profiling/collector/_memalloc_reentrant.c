#include "_memalloc_reentrant.h"

__attribute__((tls_model("global-dynamic")))
_Thread_local bool _MEMALLOC_ON_THREAD = false;

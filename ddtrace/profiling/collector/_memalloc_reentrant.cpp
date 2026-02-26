#include "_memalloc_reentrant.h"

MEMALLOC_TLS bool _MEMALLOC_ON_THREAD = false;
MEMALLOC_TLS uint64_t _MEMALLOC_REENTRY_BAILOUT_COUNT = 0;

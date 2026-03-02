#include "_memalloc_reentrant.h"

MEMALLOC_TLS memalloc_op_t _MEMALLOC_CURRENT_OP = MEMALLOC_OP_NONE;

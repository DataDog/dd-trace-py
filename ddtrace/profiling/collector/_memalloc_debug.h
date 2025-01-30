#ifndef _DDTRACE_MEMALLOC_DEBUG_H
#define _DDTRACE_MEMALLOC_DEBUG_H

#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

static const char* g_truthy_values[] = { "1", "true", "yes", "on", "enable", "enabled", NULL }; // NB the sentinel NULL

static bool
memalloc_get_bool_env(char* key)
{
    char* val = getenv(key);
    if (!val) {
        return false;
    }
    for (int i = 0; g_truthy_values[i]; i++) {
        if (strcmp(val, g_truthy_values[i]) == 0) {
            return true;
        }
    }
    return false;
}

#endif

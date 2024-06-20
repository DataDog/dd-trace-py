#include <stdlib.h>
#include <stdbool.h>

#include "dd_wrapper/include/receiver_interface.h"

int
main(void)
{
    if (!ddup_crashtracker_receiver_entry()) {
        exit(EXIT_FAILURE);
    }
    return 0;
}

#include <stdbool.h>
#include <stdlib.h>

#include "dd_wrapper/include/receiver_interface.h"

int
main(void)
{
    if (!crashtracker_receiver_entry()) {
        exit(EXIT_FAILURE);
    }
    return 0;
}

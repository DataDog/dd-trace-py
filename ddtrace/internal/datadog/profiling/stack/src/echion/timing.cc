#include <echion/timing.h>

microsecond_t
gettime()
{
#if defined PL_LINUX
    struct timespec ts;
    if (clock_gettime(CLOCK_BOOTTIME, &ts))
        return 0;
    return TS_TO_MICROSECOND(ts);
#elif defined PL_DARWIN
    mach_timespec_t ts;
    clock_get_time(cclock, &ts);
    return TS_TO_MICROSECOND(ts);
#endif
}

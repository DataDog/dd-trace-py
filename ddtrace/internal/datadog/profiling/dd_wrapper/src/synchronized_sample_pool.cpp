#include "synchronized_sample_pool.hpp"

#include "libdatadog_helpers.hpp"

extern "C"
{
#include "datadog/common.h"
}

namespace Datadog {

void
sample_delete_fn(void* sample)
{
    delete static_cast<Sample*>(sample);
}

SynchronizedSamplePool::SynchronizedSamplePool(size_t capacity)
{
    ddog_ArrayQueue_NewResult array_queue_new_result = ddog_ArrayQueue_new(capacity, sample_delete_fn);
    if (array_queue_new_result.tag != DDOG_ARRAY_QUEUE_NEW_RESULT_OK) {
        auto err = array_queue_new_result.err;
        std::string errmsg = err_to_msg(&err, "Failed to create sample pool");
        std::cerr << errmsg << std::endl;
        ddog_Error_drop(&err);
        pool = nullptr;
    }
    pool = std::unique_ptr<ddog_ArrayQueue, Deleter>(array_queue_new_result.ok);
}

std::optional<Sample*>
SynchronizedSamplePool::get_sample()
{
    ddog_ArrayQueue_PopResult pop_result = ddog_ArrayQueue_pop(pool.get());
    if (pop_result.tag == DDOG_ARRAY_QUEUE_POP_RESULT_EMPTY) {
        return std::nullopt;
    }
    return static_cast<Sample*>(pop_result.ok);
}

std::optional<Sample*>
SynchronizedSamplePool::return_sample(Sample* sample)
{
    ddog_ArrayQueue_PushResult push_result = ddog_ArrayQueue_push(pool.get(), sample);
    if (push_result.tag == DDOG_ARRAY_QUEUE_PUSH_RESULT_FULL) {
        return static_cast<Sample*>(push_result.full);
    }
    return std::nullopt;
}
} // namespace Datadog

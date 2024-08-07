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
    if (array_queue_new_result.tag == DDOG_ARRAY_QUEUE_NEW_RESULT_OK) {
        pool = std::unique_ptr<ddog_ArrayQueue, Deleter>(array_queue_new_result.ok);
    } else {
        auto err = array_queue_new_result.err;
        std::string errmsg = err_to_msg(&err, "Failed to create sample pool");
        std::cerr << errmsg << std::endl;
        ddog_Error_drop(&err);
        pool = nullptr;
    }
}

std::optional<Sample*>
SynchronizedSamplePool::get_sample()
{
    if (pool == nullptr) {
        // It's actually ok to call ddog_ArrayQueue_* methods with a nullptr,
        // they will return an error result, but we already have printed out
        // an error message in the constructor, so check for nullptr here to
        // avoid spamming the error message.
        return std::nullopt;
    }
    ddog_ArrayQueue_PopResult pop_result = ddog_ArrayQueue_pop(pool.get());
    if (pop_result.tag == DDOG_ARRAY_QUEUE_POP_RESULT_OK) {
        return static_cast<Sample*>(pop_result.ok);
    } else if (pop_result.tag == DDOG_ARRAY_QUEUE_POP_RESULT_EMPTY) {
        return std::nullopt;
    } else if (pop_result.tag == DDOG_ARRAY_QUEUE_POP_RESULT_ERR) {
        auto err = pop_result.err;
        std::string errmsg = err_to_msg(&err, "Failed to get sample from pool");
        std::cerr << errmsg << std::endl;
        ddog_Error_drop(&err);
        return std::nullopt;
    }
    // To silence the warning about missing return statement
    return std::nullopt;
}

std::optional<Sample*>
SynchronizedSamplePool::return_sample(Sample* sample)
{
    if (pool == nullptr) {
        return std::nullopt;
    }
    ddog_ArrayQueue_PushResult push_result = ddog_ArrayQueue_push(pool.get(), sample);
    if (push_result.tag == DDOG_ARRAY_QUEUE_PUSH_RESULT_OK) {
        return std::nullopt;
    } else if (push_result.tag == DDOG_ARRAY_QUEUE_PUSH_RESULT_FULL) {
        return static_cast<Sample*>(push_result.full);
    } else if (push_result.tag == DDOG_ARRAY_QUEUE_PUSH_RESULT_ERR) {
        auto err = push_result.err;
        std::string errmsg = err_to_msg(&err, "Failed to return sample to pool");
        std::cerr << errmsg << std::endl;
        ddog_Error_drop(&err);
        return std::nullopt;
    }
    // To silence the warning about missing return statement
    return std::nullopt;
}
} // namespace Datadog

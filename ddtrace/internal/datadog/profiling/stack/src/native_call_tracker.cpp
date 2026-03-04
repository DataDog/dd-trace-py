#include "native_call_tracker.hpp"

#include <mutex>
#include <shared_mutex>
#include <string>

namespace Datadog {

void
NativeCallRegistry::register_call_site(uintptr_t code_ptr,
                                       int offset_bytes,
                                       int first_lineno,
                                       std::string name,
                                       std::string module)
{
    CallSiteKey key{ code_ptr, offset_bytes, first_lineno };
    std::unique_lock lock(mtx);
    auto it = call_sites.find(key);
    if (it == call_sites.end()) {
        call_sites.emplace(key, NativeCallEntry{ std::move(name), std::move(module) });
    }
}

const NativeCallEntry*
NativeCallRegistry::lookup(uintptr_t code_ptr, int offset_bytes, int first_lineno)
{
    CallSiteKey key{ code_ptr, offset_bytes, first_lineno };
    std::shared_lock lock(mtx);
    auto it = call_sites.find(key);
    if (it != call_sites.end()) {
        return &it->second;
    }
    return nullptr;
}

void
NativeCallRegistry::reset()
{
    std::unique_lock lock(mtx);
    call_sites.clear();
}

void
NativeCallRegistry::postfork_child()
{
    // NB placement-new to re-init and leak the mutex because doing anything else is UB
    new (&get_instance().mtx) std::shared_mutex();
    get_instance().reset();
}

} // namespace Datadog

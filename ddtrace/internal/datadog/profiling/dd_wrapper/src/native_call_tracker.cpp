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

std::optional<std::reference_wrapper<NativeCallEntry>>
NativeCallRegistry::lookup(uintptr_t code_ptr, int offset_bytes, int first_lineno)
{
    CallSiteKey key{ code_ptr, offset_bytes, first_lineno };
    std::shared_lock lock(mtx);

    auto it = call_sites.find(key);
    if (it != call_sites.end()) {
        return std::ref(it->second);
    }

    return std::nullopt;
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
    // NB placement-new to re-init the mutex because doing anything else is UB.
    // We intentionally do NOT clear call_sites: after fork the code objects live
    // at the same addresses, and sys.monitoring has already returned DISABLE for
    // every call site seen in the parent. Clearing would lose native frame info
    // with no way to re-populate it.
    new (&mtx) std::shared_mutex();
}

} // namespace Datadog

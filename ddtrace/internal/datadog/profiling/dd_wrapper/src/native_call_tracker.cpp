#include "native_call_tracker.hpp"

#include "fork_utils.hpp"
#include <functional>
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
        if (call_sites.size() >= max_call_sites) {
            return;
        }
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
    // TODO: Lock mtx in prefork() and unlock here instead of placement-new.
    // Currently mtx is not quiesced before fork, so if another thread holds it
    // during fork the child inherits a locked mutex owned by a dead thread.
    // Placement-new is the workaround until prefork covers this mutex.
    //
    // We intentionally do NOT clear call_sites: after fork the code objects live
    // at the same addresses, and sys.monitoring has already returned DISABLE for
    // every call site seen in the parent. Clearing would lose native frame info
    // with no way to re-populate it.
    reset_mutex_after_fork(mtx);
}

size_t
NativeCallRegistry::size() const
{
    std::shared_lock lock(mtx);
    return call_sites.size();
}

} // namespace Datadog

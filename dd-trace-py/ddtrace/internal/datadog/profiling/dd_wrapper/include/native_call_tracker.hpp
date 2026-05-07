#pragma once

#include <cstdint>
#include <optional>
#include <shared_mutex>
#include <string>
#include <unordered_map>

namespace Datadog {

struct CallSiteKey
{
    uintptr_t code_ptr;
    int offset_bytes;
    int first_lineno; // Guards against code object address reuse after GC
    bool operator==(const CallSiteKey& other) const
    {
        return code_ptr == other.code_ptr && offset_bytes == other.offset_bytes && first_lineno == other.first_lineno;
    }
};

struct CallSiteKeyHash
{
    size_t operator()(const CallSiteKey& k) const
    {
        // Boost-style hash combine for better collision resistance
        size_t h = std::hash<uintptr_t>()(k.code_ptr);
        h ^= std::hash<int>()(k.offset_bytes) + 0x9e3779b9 + (h << 6) + (h >> 2);
        h ^= std::hash<int>()(k.first_lineno) + 0x9e3779b9 + (h << 6) + (h >> 2);
        return h;
    }
};

struct NativeCallEntry
{
    std::string name;
    std::string module;
};

class NativeCallRegistry
{
  public:
    NativeCallRegistry() = default;
    ~NativeCallRegistry() = default;

    NativeCallRegistry(NativeCallRegistry const&) = delete;
    NativeCallRegistry& operator=(NativeCallRegistry const&) = delete;

    void register_call_site(uintptr_t code_ptr,
                            int offset_bytes,
                            int first_lineno,
                            std::string name,
                            std::string module);

    // Checks if there is a known native call metadata object for a bytecode location and
    // returns it when found.
    // Note: it is safe to return a reference to the NativeCallEntry because according to
    // the standard, "References and pointers to either key or data stored in the container
    // are only invalidated by erasing that element, even when the corresponding iterator
    // is invalidated."
    // All the emplace's we do happen under the lock, which means there can never be a
    // duplicate emplace happening (as we check for existence first).
    std::optional<std::reference_wrapper<NativeCallEntry>> lookup(uintptr_t code_ptr,
                                                                  int offset_bytes,
                                                                  int first_lineno);

    // Clears the registry.
    // Note: this frees the NativeCallEntry's, meaning any reference to a NativeCallEntry from
    // the map is invalid after calling reset.
    // Ensure reset is only ever called after users of lookup (and its return values) have been
    // stopped (typically, the sampling thread).
    void reset();

    void postfork_child();

  private:
    std::shared_mutex mtx;
    std::unordered_map<CallSiteKey, NativeCallEntry, CallSiteKeyHash> call_sites;
};

} // namespace Datadog

#pragma once

#include <cstdint>
#include <shared_mutex>
#include <string>
#include <unordered_map>

namespace Datadog {

struct CallSiteKey
{
    uintptr_t code_ptr;
    int lasti;
    bool operator==(const CallSiteKey& other) const { return code_ptr == other.code_ptr && lasti == other.lasti; }
};

struct CallSiteKeyHash
{
    size_t operator()(const CallSiteKey& k) const
    {
        return std::hash<uintptr_t>()(k.code_ptr) ^ (std::hash<int>()(k.lasti) * 2654435761u);
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
    static NativeCallRegistry& get_instance()
    {
        static NativeCallRegistry instance;
        return instance;
    }

    NativeCallRegistry(NativeCallRegistry const&) = delete;
    NativeCallRegistry& operator=(NativeCallRegistry const&) = delete;

    void register_call_site(uintptr_t code_ptr, int lasti, std::string name, std::string module);
    const NativeCallEntry* lookup(uintptr_t code_ptr, int lasti);
    void reset();

    static void postfork_child();

  private:
    std::shared_mutex mtx;
    std::unordered_map<CallSiteKey, NativeCallEntry, CallSiteKeyHash> call_sites;

    NativeCallRegistry() = default;
    ~NativeCallRegistry() = default;
};

} // namespace Datadog

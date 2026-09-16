#include "libdatadog_helpers.hpp"

#include "profiler_state.hpp"

std::optional<Datadog::string_id>
Datadog::intern_string(std::string_view s)
{
    auto dict = ProfilerState::get().borrow_dictionary();
    if (!dict.has_value()) {
        return std::nullopt;
    }

    // R&D caveat: the C FFI path used CONVERT_LOSSY. The CXX API takes rust::Str,
    // so production parity may require a CXX lossy insertion variant.
    ddprof::DictionaryStringId id{};
    if (!dict->value.intern_string(to_rust_str(s), id)) {
        return std::nullopt;
    }
    return id;
}

std::optional<Datadog::function_id>
Datadog::intern_function(string_id name, string_id filename)
{
    auto dict = ProfilerState::get().borrow_dictionary();
    if (!dict.has_value()) {
        return std::nullopt;
    }

    ddprof::DictionaryFunctionId id{};
    if (!dict->value.intern_function(
          ddprof::DictionaryFunction{
            name,
            {}, // No support for system_name in Python; default string id means empty string.
            filename,
          },
          id)) {
        return std::nullopt;
    }
    return id;
}

namespace Datadog::internal {

std::optional<ddprof::DictionaryStringId>
to_interned_string(ExportLabelKey key)
{
    auto& state = ProfilerState::get();
    const auto idx = static_cast<size_t>(key);

    if (idx >= state.label_cache.size()) {
        return std::nullopt;
    }

    // Check cache first (relaxed is fine - benign race, worst case is interning twice)
    auto string_id = state.label_cache[idx].load(std::memory_order_relaxed);
    if (string_id.handle == nullptr) {
        auto interned = intern_string(to_string(key));
        if (!interned) {
            return std::nullopt;
        }
        string_id = interned.value();
        state.label_cache[idx].store(string_id, std::memory_order_relaxed);
    }

    return string_id;
}

} // namespace Datadog::internal

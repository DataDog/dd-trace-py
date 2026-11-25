#pragma once

// Reads the span labels of a contextvars.Context from raw memory.
//
// The tracer keeps the active span in DD_CONTEXTVAR. ContextLookup finds that variable in a Context's HAMT,
// resolves its value the way DefaultContextProvider._update_active does, and computes the labels
// StackCollector._link_span would attach. Every load goes through the Copy function the caller provides,
// and nothing here allocates, locks or calls into Python, so the sampler thread (with copy_memory) and a
// signal handler (with guarded reads) can share it.
//
// The layouts come from the tracer's profiler layout capsule (src/native/profiler_layout/profiler_layout.h)
// and from the CPython mirrors in echion/cpython/context.h, checked at startup in context_labels.cc.

#include <echion/cpython/context.h>

#include <profiler_layout.h>

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <optional>
#include <string_view>

struct ContextLabels
{
    uint64_t span_id = 0;
    uint64_t local_root_span_id = 0;
    uint32_t span_type_id = DD_SPAN_TYPE_NONE; // id in the tracer's span type table
};

enum class ContextReadStatus : uint8_t
{
    Ok,       // labels found
    NoLabels, // no span: missing key, None, a finished span with nothing to fall back to, or span id 0
    Disabled, // the reader is not initialized
    // Failures. Each bumps a diagnostic counter.
    CopyFailed,        // a copy failed or a pointer was invalid
    CopyBudget,        // the lookup ran out of copies
    BadContextType,    // the Context header has the wrong type
    BadHamtType,       // ctx_vars is not a HAMT
    BadNode,           // a HAMT node has an unknown type or inconsistent contents
    TooDeep,           // a non-collision node below the last HAMT level
    CollisionTooLarge, // a collision node with more entries than the cap
    BadValueType,      // the value, or an object it points to, has an unexpected type
    HopLimit,          // resolve followed more parents than the cap
    Count,
};

constexpr bool
is_failure(ContextReadStatus status)
{
    return status >= ContextReadStatus::CopyFailed && status < ContextReadStatus::Count;
}

const char*
context_read_status_name(ContextReadStatus status);

// Failure counts, indexed by ContextReadStatus. Lock-free, so a signal handler can bump them.
inline std::array<std::atomic<uint64_t>, static_cast<size_t>(ContextReadStatus::Count)> g_context_read_failures{};

inline void
count_context_read(ContextReadStatus status)
{
    if (is_failure(status)) {
        g_context_read_failures[static_cast<size_t>(status)].fetch_add(1, std::memory_order_relaxed);
    }
}

// Upper bound for ContextLookupLimits::collision_entries: collision pairs are copied to the stack.
constexpr size_t MAX_COLLISION_ENTRIES = 8;

struct ContextLookupLimits
{
    // Levels 0 to 6 consume 5 bits of the 32-bit hash each. Only a collision node may sit at level 7.
    uint32_t hamt_levels = 8;
    uint32_t collision_entries = MAX_COLLISION_ENTRIES;
    uint32_t resolve_hops = 64;
    uint32_t copies = 32;
    uint32_t type_bases = 8;
};

// Everything ContextLookup needs to know about the CPython and tracer objects. Addresses are integers
// because they come from memory that has not been validated.
struct ContextReaderLayout
{
    // CPython types.
    uintptr_t context_type = 0;
    uintptr_t hamt_type = 0;
    uintptr_t bitmap_type = 0;
    uintptr_t array_type = 0;
    uintptr_t collision_type = 0;
    uintptr_t none = 0;

    // The key to look up, compared by identity, and its 32-bit HAMT hash.
    uintptr_t key = 0;
    uint32_t key_hash = 0;

    // Tracer types. span_type points to the descriptor's atomic slot for ddtrace._trace.span.Span, which
    // stays NULL until that module is imported.
    uintptr_t span_data_type = 0;
    const void* const* span_type = nullptr;
    uintptr_t tracer_context_type = 0;

    dd_span_offsets_t span{};
    dd_context_offsets_t context{};

    // Bytes copied from the start of a value of unknown type. When the value fits in both a span and a
    // Context, one copy reads its header and fields. 0 means copy the header first.
    uint32_t value_copy_size = 0;
};

// Largest copy of one object. Validated against the tracer's offsets at startup.
constexpr size_t CONTEXT_READER_MAX_OBJECT_COPY = 256;

template<typename Copy>
class ContextLookup
{
  public:
    // copy(address, buffer, size) copies size bytes and returns false when any of them is unreadable.
    ContextLookup(const ContextReaderLayout& layout, Copy& copy, ContextLookupLimits limits = {})
      : layout_(layout)
      , copy_(copy)
      , limits_(limits)
    {
    }

    // Reads the labels of the Context at ctx. resolved, when given, receives the address of the span or
    // Context that resolve() picked, or 0. A NULL ctx has no labels.
    ContextReadStatus read(uintptr_t ctx, ContextLabels& labels, uintptr_t* resolved = nullptr)
    {
        if (resolved != nullptr) {
            *resolved = 0;
        }
        if (ctx == 0) {
            return ContextReadStatus::NoLabels;
        }

        uintptr_t value = 0;
        ContextReadStatus status = find_value(ctx, value);
        if (status != ContextReadStatus::Ok) {
            return status;
        }

        Object object;
        status = resolve(value, object);
        if (status != ContextReadStatus::Ok) {
            return status;
        }
        if (resolved != nullptr) {
            *resolved = object.address;
        }
        return compute_labels(object, labels);
    }

    // Finds the value of layout.key in the Context at ctx. Ok with value 0 means the key is missing.
    ContextReadStatus find_value(uintptr_t ctx, uintptr_t& value)
    {
        value = 0;

        EchionContextObject context;
        ContextReadStatus status = fetch(ctx, &context, sizeof(context));
        if (status != ContextReadStatus::Ok) {
            return status;
        }
        if (type_of(&context) != layout_.context_type) {
            return ContextReadStatus::BadContextType;
        }

        EchionHamtObject hamt;
        status = fetch(reinterpret_cast<uintptr_t>(context.ctx_vars), &hamt, sizeof(hamt));
        if (status != ContextReadStatus::Ok) {
            return status;
        }
        if (type_of(&hamt) != layout_.hamt_type) {
            return ContextReadStatus::BadHamtType;
        }

        uintptr_t node = reinterpret_cast<uintptr_t>(hamt.h_root);
        for (uint32_t level = 0; level < limits_.hamt_levels; ++level) {
            if (node == 0) {
                return ContextReadStatus::BadNode;
            }

            // Large enough for the bitmap and collision headers, and within the smallest node.
            EchionHamtBitmapHeader header;
            static_assert(sizeof(EchionHamtBitmapHeader) >= sizeof(EchionHamtCollisionHeader));
            status = fetch(node, &header, sizeof(header));
            if (status != ContextReadStatus::Ok) {
                return status;
            }
            const uintptr_t type = type_of(&header);
            const Py_ssize_t size = header.ob_base.ob_size;

            if (type == layout_.collision_type) {
                return find_in_collision(node, size, value);
            }

            if (level + 1 >= limits_.hamt_levels || level * 5 >= 32) {
                // Every hash bit is used up: only a collision node can be this deep.
                return ContextReadStatus::TooDeep;
            }
            const uint32_t slot = (layout_.key_hash >> (level * 5)) & 0x1f;

            if (type == layout_.bitmap_type) {
                const uint32_t bitmap = header.b_bitmap;
                const uint32_t bit = 1u << slot;
                if (size < 0 || size != 2 * static_cast<Py_ssize_t>(__builtin_popcount(bitmap))) {
                    return ContextReadStatus::BadNode;
                }
                if ((bitmap & bit) == 0) {
                    return ContextReadStatus::Ok; // missing
                }
                const uint32_t index = __builtin_popcount(bitmap & (bit - 1));
                uintptr_t pair[2];
                status =
                  fetch(node + ECHION_HAMT_BITMAP_ARRAY_OFFSET + 2 * index * sizeof(uintptr_t), pair, sizeof(pair));
                if (status != ContextReadStatus::Ok) {
                    return status;
                }
                if (pair[0] == 0) {
                    node = pair[1]; // a child node
                    continue;
                }
                if (pair[0] == layout_.key) {
                    value = pair[1];
                }
                return ContextReadStatus::Ok;
            }

            if (type == layout_.array_type) {
                uintptr_t child = 0;
                status = fetch(node + ECHION_HAMT_ARRAY_OFFSET + slot * sizeof(uintptr_t), &child, sizeof(child));
                if (status != ContextReadStatus::Ok) {
                    return status;
                }
                if (child == 0) {
                    return ContextReadStatus::Ok; // missing
                }
                node = child;
                continue;
            }

            return ContextReadStatus::BadNode;
        }
        return ContextReadStatus::TooDeep;
    }

    enum class Kind : uint8_t
    {
        Other,
        Span,
        Context,
    };

    // A copy of one object, from its start to the end of the fields the reader uses.
    struct Object
    {
        uintptr_t address = 0;
        Kind kind = Kind::Other;
        alignas(8) unsigned char bytes[CONTEXT_READER_MAX_OBJECT_COPY];

        template<typename T>
        T field(uint32_t offset) const
        {
            T value;
            std::memcpy(&value, bytes + offset, sizeof(T));
            return value;
        }
    };

    // Mirrors DefaultContextProvider._update_active without activating anything. An unfinished span or a
    // Context is the answer. A finished span walks to its parent or, without one, to its parent Context
    // when that Context reactivates.
    ContextReadStatus resolve(uintptr_t value, Object& object)
    {
        if (value == 0 || value == layout_.none) {
            return ContextReadStatus::NoLabels;
        }
        ContextReadStatus status = load(value, Kind::Other, object);
        if (status != ContextReadStatus::Ok) {
            return status;
        }
        if (object.kind == Kind::Other) {
            return ContextReadStatus::BadValueType;
        }

        for (uint32_t hops = 0;; ++hops) {
            if (object.kind == Kind::Context) {
                return ContextReadStatus::Ok;
            }
            if (!span_flag(object, layout_.span.finished)) {
                return ContextReadStatus::Ok;
            }
            const auto parent = object.template field<uintptr_t>(layout_.span.parent);
            if (parent == 0) {
                const auto parent_context = object.template field<uintptr_t>(layout_.span.parent_context);
                if (parent_context == 0) {
                    return ContextReadStatus::NoLabels;
                }
                status = load(parent_context, Kind::Context, object);
                if (status != ContextReadStatus::Ok) {
                    return status;
                }
                return object.template field<uint8_t>(layout_.context.reactivate) ? ContextReadStatus::Ok
                                                                                  : ContextReadStatus::NoLabels;
            }
            if (hops >= limits_.resolve_hops) {
                return ContextReadStatus::HopLimit;
            }
            status = load(parent, Kind::Span, object);
            if (status != ContextReadStatus::Ok) {
                return status;
            }
        }
    }

    // The labels StackCollector._link_span attaches for object, except that a span id of 0 gives none.
    ContextReadStatus compute_labels(const Object& object, ContextLabels& labels)
    {
        if (object.kind == Kind::Context) {
            const auto span_id = object.template field<uint64_t>(layout_.context.span_id);
            if (span_id == 0) {
                return ContextReadStatus::NoLabels;
            }
            const auto root_id = object.template field<uint64_t>(layout_.context.local_root_span_id);
            labels.span_id = span_id;
            labels.local_root_span_id = root_id != 0 ? root_id : span_id;
            labels.span_type_id = object.template field<uint32_t>(layout_.context.span_type_id);
            return ContextReadStatus::Ok;
        }

        const auto span_id = object.template field<uint64_t>(layout_.span.span_id);
        if (span_id == 0) {
            return ContextReadStatus::NoLabels;
        }

        // The local root, or the span itself when it is its own root.
        uint64_t root_id = span_id;
        uint32_t root_type = object.template field<uint32_t>(layout_.span.span_type_id);
        const auto local_root = object.template field<uintptr_t>(layout_.span.local_root);
        if (local_root != 0 && local_root != object.address) {
            ContextReadStatus status = load(local_root, Kind::Span, scratch_);
            if (status != ContextReadStatus::Ok) {
                return status;
            }
            root_id = scratch_.template field<uint64_t>(layout_.span.span_id);
            root_type = scratch_.template field<uint32_t>(layout_.span.span_type_id);
        }

        labels.span_id = span_id;
        labels.local_root_span_id = root_id;
        labels.span_type_id = root_type;

        // A distributed child: no parent span, but a parent id and a parent Context. Prefer the Context's
        // propagated local root id, then its span id, then the local root's; and its propagated span type,
        // then the local root's.
        const auto parent = object.template field<uintptr_t>(layout_.span.parent);
        const auto parent_id = object.template field<uint64_t>(layout_.span.parent_id);
        const auto parent_context = object.template field<uintptr_t>(layout_.span.parent_context);
        if (parent == 0 && parent_id != 0 && parent_context != 0) {
            ContextReadStatus status = load(parent_context, Kind::Context, scratch_);
            if (status != ContextReadStatus::Ok) {
                return status;
            }
            const auto propagated_root = scratch_.template field<uint64_t>(layout_.context.local_root_span_id);
            const auto context_span_id = scratch_.template field<uint64_t>(layout_.context.span_id);
            const auto propagated_type = scratch_.template field<uint32_t>(layout_.context.span_type_id);
            if (propagated_root != 0) {
                labels.local_root_span_id = propagated_root;
            } else if (context_span_id != 0) {
                labels.local_root_span_id = context_span_id;
            }
            if (propagated_type != DD_SPAN_TYPE_NONE) {
                labels.span_type_id = propagated_type;
            }
        }
        return ContextReadStatus::Ok;
    }

    uint32_t copies() const { return copies_; }

  private:
    static uintptr_t type_of(const void* object)
    {
        uintptr_t type;
        std::memcpy(&type, static_cast<const unsigned char*>(object) + offsetof(PyObject, ob_type), sizeof(type));
        return type;
    }

    ContextReadStatus fetch(uintptr_t address, void* buffer, size_t size)
    {
        if (address < 4096 || address % alignof(void*) != 0) {
            return ContextReadStatus::CopyFailed;
        }
        if (copies_ >= limits_.copies) {
            return ContextReadStatus::CopyBudget;
        }
        ++copies_;
        return copy_(address, buffer, size) ? ContextReadStatus::Ok : ContextReadStatus::CopyFailed;
    }

    static uint32_t end_of(uint32_t offset, uint32_t size) { return offset + size; }

    uint32_t copy_size(Kind kind) const
    {
        return kind == Kind::Span ? end_of(layout_.span.copy_offset, layout_.span.copy_size)
                                  : end_of(layout_.context.copy_offset, layout_.context.copy_size);
    }

    // The span classes: SpanData, Span, and subclasses found by a bounded walk of tp_base. The last
    // subclass found is remembered, since a chain of spans usually shares one class.
    ContextReadStatus classify(uintptr_t type, Kind& kind)
    {
        if (type != 0 && type == subclass_) {
            kind = subclass_kind_;
            return ContextReadStatus::Ok;
        }
        const uintptr_t subclass = type;
        const auto span_type = reinterpret_cast<uintptr_t>(
          layout_.span_type != nullptr ? __atomic_load_n(layout_.span_type, __ATOMIC_ACQUIRE) : nullptr);
        for (uint32_t bases = 0;; ++bases) {
            if (type == layout_.span_data_type || (span_type != 0 && type == span_type)) {
                kind = Kind::Span;
                remember(subclass, bases, kind);
                return ContextReadStatus::Ok;
            }
            if (type == layout_.tracer_context_type) {
                kind = Kind::Context;
                remember(subclass, bases, kind);
                return ContextReadStatus::Ok;
            }
            if (type == 0 || bases >= limits_.type_bases) {
                kind = Kind::Other;
                return ContextReadStatus::Ok;
            }
            uintptr_t base = 0;
            ContextReadStatus status = fetch(type + offsetof(PyTypeObject, tp_base), &base, sizeof(base));
            if (status != ContextReadStatus::Ok) {
                return status;
            }
            type = base;
        }
    }

    void remember(uintptr_t subclass, uint32_t bases, Kind kind)
    {
        if (bases > 0) {
            subclass_ = subclass;
            subclass_kind_ = kind;
        }
    }

    // Copies the object at address and checks its type. expected is Other when any type may be there.
    ContextReadStatus load(uintptr_t address, Kind expected, Object& object)
    {
        object.address = address;
        object.kind = Kind::Other;

        uint32_t size = expected == Kind::Other ? layout_.value_copy_size : copy_size(expected);
        if (size == 0) {
            size = sizeof(PyObject);
        }
        ContextReadStatus status = fetch(address, object.bytes, size);
        if (status != ContextReadStatus::Ok) {
            return status;
        }

        const uintptr_t type = type_of(object.bytes);
        Kind kind = Kind::Other;
        status = classify(type, kind);
        if (status != ContextReadStatus::Ok) {
            return status;
        }
        if (expected != Kind::Other && kind != expected) {
            return ContextReadStatus::BadValueType;
        }
        object.kind = kind;

        // A value whose type was unknown: copy its fields now.
        if (kind != Kind::Other && size < copy_size(kind)) {
            status = fetch(address, object.bytes, copy_size(kind));
            if (status != ContextReadStatus::Ok) {
                return status;
            }
            if (type_of(object.bytes) != type) {
                return ContextReadStatus::BadValueType; // freed and reused between the copies
            }
        }
        return ContextReadStatus::Ok;
    }

    ContextReadStatus find_in_collision(uintptr_t node, Py_ssize_t size, uintptr_t& value)
    {
        if (size < 0 || size % 2 != 0) {
            return ContextReadStatus::BadNode;
        }
        if (static_cast<size_t>(size) > 2 * static_cast<size_t>(limits_.collision_entries)) {
            return ContextReadStatus::CollisionTooLarge;
        }
        uintptr_t pairs[2 * MAX_COLLISION_ENTRIES];
        if (static_cast<size_t>(size) > sizeof(pairs) / sizeof(pairs[0])) {
            return ContextReadStatus::CollisionTooLarge;
        }
        if (size == 0) {
            return ContextReadStatus::BadNode; // CPython never keeps an empty collision node
        }
        ContextReadStatus status =
          fetch(node + ECHION_HAMT_COLLISION_ARRAY_OFFSET, pairs, static_cast<size_t>(size) * sizeof(uintptr_t));
        if (status != ContextReadStatus::Ok) {
            return status;
        }
        for (Py_ssize_t i = 0; i < size; i += 2) {
            if (pairs[i] == layout_.key) {
                value = pairs[i + 1];
                return ContextReadStatus::Ok;
            }
        }
        return ContextReadStatus::Ok; // missing
    }

    bool span_flag(const Object& object, uint32_t offset) const { return object.template field<uint8_t>(offset) != 0; }

    const ContextReaderLayout& layout_;
    Copy& copy_;
    ContextLookupLimits limits_;
    uint32_t copies_ = 0;
    uintptr_t subclass_ = 0;
    Kind subclass_kind_ = Kind::Other;
    Object scratch_;
};

// The copy function the sampler thread uses: Echion's fault-safe copy_memory.
struct EchionCopy
{
    bool operator()(uintptr_t address, void* buffer, size_t size) const;
};

// Process-wide reader state, set by init_context_reader().
struct ContextReader
{
    std::atomic<bool> enabled{ false };
    const char* error = "not initialized";
    ContextReaderLayout layout;
    const dd_profiler_layout_t* tracer = nullptr;
};

// The 32-bit hash CPython's HAMT derives from a Py_hash_t (hamt_hash() in Python/hamt.c).
uint32_t
hamt_hash(Py_hash_t hash);

// Fills the CPython fields of layout from live objects, and checks the mirrors in echion/cpython/context.h
// by looking up variables in bitmap, array and collision nodes. Needs the GIL. Returns NULL on success,
// otherwise what did not match.
const char*
calibrate_context_layout(ContextReaderLayout& layout);

// Imports the tracer's layout capsule and checks the CPython Context and HAMT layouts against live
// objects. Needs the GIL. Runs once; later calls return the first result. On any mismatch the reader
// stays disabled and the Python error, if any, is cleared.
bool
init_context_reader();

const ContextReader&
context_reader();

// Reads the labels of a Context with copy_memory. Counts failures in g_context_read_failures.
ContextReadStatus
read_context_labels(uintptr_t ctx, ContextLabels& labels, uintptr_t* resolved = nullptr);

// The sampler's entry point: the labels of a Context, or nullopt for no span or a failed read. Failures
// are still counted.
std::optional<ContextLabels>
read_labels(PyContext* ctx);

// The span type for an id, or an empty view for no type or a type the table could not hold. The table
// is append-only and never freed, so the view stays valid.
std::string_view
span_type_name(uint32_t span_type_id);

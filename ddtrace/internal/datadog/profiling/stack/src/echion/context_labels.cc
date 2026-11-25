#include <echion/context_labels.h>

#include <echion/vm.h>

#include <algorithm>
#include <cstring>

namespace {

ContextReader g_reader;
bool g_reader_initialized = false;
} // namespace

uint32_t
hamt_hash(Py_hash_t hash)
{
#if SIZEOF_PY_HASH_T <= 4
    return static_cast<uint32_t>(hash);
#else
    const auto xored =
      static_cast<int32_t>(static_cast<int32_t>(hash & 0xffffffffL) ^ static_cast<int32_t>(hash >> 32));
    return static_cast<uint32_t>(xored == -1 ? -2 : xored);
#endif
}

namespace {

uintptr_t
address_of(const void* object)
{
    return reinterpret_cast<uintptr_t>(object);
}

uintptr_t
type_address(PyObject* object)
{
    return address_of(Py_TYPE(object));
}

bool
has_type_name(PyObject* object, const char* name)
{
    return object != nullptr && std::strcmp(Py_TYPE(object)->tp_name, name) == 0;
}

// Owns a new reference.
struct Ref
{
    PyObject* object;
    explicit Ref(PyObject* o)
      : object(o)
    {
    }
    ~Ref() { Py_XDECREF(object); }
    Ref(const Ref&) = delete;
    Ref& operator=(const Ref&) = delete;
    PyObject* get() const { return object; }
};

// Sets var to value in ctx without touching the current Context.
bool
set_in_context(PyObject* ctx, PyObject* var, PyObject* value)
{
    if (PyContext_Enter(ctx) < 0) {
        return false;
    }
    PyObject* token = PyContextVar_Set(var, value);
    const bool ok = token != nullptr;
    Py_XDECREF(token);
    if (PyContext_Exit(ctx) < 0) {
        return false;
    }
    return ok;
}

PyObject*
hamt_root(PyObject* ctx)
{
    auto* hamt = reinterpret_cast<EchionHamtObject*>(reinterpret_cast<EchionContextObject*>(ctx)->ctx_vars);
    return hamt->h_root;
}

// Looks var up in ctx with the reader and checks that it finds value.
bool
reader_finds(const ContextReaderLayout& base, PyObject* ctx, PyObject* var, PyObject* value)
{
    ContextReaderLayout layout = base;
    layout.key = address_of(var);
    layout.key_hash = hamt_hash(PyObject_Hash(var));
    EchionCopy copy;
    ContextLookup<EchionCopy> lookup(layout, copy);
    uintptr_t found = 0;
    return lookup.find_value(address_of(ctx), found) == ContextReadStatus::Ok && found == address_of(value);
}

} // namespace

const char*
calibrate_context_layout(ContextReaderLayout& layout)
{
    layout.context_type = address_of(&PyContext_Type);
    layout.none = address_of(Py_None);

    Ref ctx(PyContext_New());
    if (ctx.get() == nullptr) {
        return "cannot create a Context";
    }
    auto* context = reinterpret_cast<EchionContextObject*>(ctx.get());
    if (type_address(ctx.get()) != layout.context_type || !has_type_name(context->ctx_vars, "hamt")) {
        return "unexpected Context layout";
    }
    layout.hamt_type = type_address(context->ctx_vars);
    PyObject* root = hamt_root(ctx.get());
    if (!has_type_name(root, "hamt_bitmap_node")) {
        return "unexpected empty HAMT root";
    }
    layout.bitmap_type = type_address(root);

    // Enough variables for the root to become an array node (more than 16 distinct 5-bit slots).
    constexpr int VARS = 256;
    PyObject* vars[VARS] = {};
    PyObject* values[VARS] = {};
    const char* error = nullptr;
    int count = 0;
    for (; count < VARS; ++count) {
        vars[count] = PyContextVar_New("ddtrace_profiling_calibration", nullptr);
        values[count] = PyLong_FromLong(1000000 + count); // distinct objects
        if (vars[count] == nullptr || values[count] == nullptr ||
            !set_in_context(ctx.get(), vars[count], values[count])) {
            error = "cannot set a ContextVar";
            ++count;
            break;
        }
        if (has_type_name(hamt_root(ctx.get()), "hamt_array_node")) {
            ++count;
            break;
        }
    }
    if (error == nullptr) {
        auto* hamt = reinterpret_cast<EchionHamtObject*>(context->ctx_vars);
        if (!has_type_name(hamt_root(ctx.get()), "hamt_array_node")) {
            error = "no HAMT array node";
        } else if (hamt->h_count != count) {
            error = "unexpected HAMT layout";
        } else {
            layout.array_type = type_address(hamt_root(ctx.get()));
        }
    }

    // Two variables with the same hash share a collision node.
    if (error == nullptr) {
        Ref collision_ctx(PyContext_New());
        Ref first(PyContextVar_New("ddtrace_profiling_calibration", nullptr));
        Ref second(PyContextVar_New("ddtrace_profiling_calibration", nullptr));
        if (collision_ctx.get() == nullptr || first.get() == nullptr || second.get() == nullptr) {
            error = "cannot create a ContextVar";
        } else {
            auto* first_var = reinterpret_cast<EchionContextVarObject*>(first.get());
            auto* second_var = reinterpret_cast<EchionContextVarObject*>(second.get());
            if (first_var->var_hash != PyObject_Hash(first.get())) {
                error = "unexpected ContextVar layout";
            } else {
                second_var->var_hash = first_var->var_hash;
                if (!set_in_context(collision_ctx.get(), first.get(), values[0]) ||
                    !set_in_context(collision_ctx.get(), second.get(), values[1])) {
                    error = "cannot set a ContextVar";
                } else {
                    PyObject* node = hamt_root(collision_ctx.get());
                    PyObject* child = nullptr;
                    if (has_type_name(node, "hamt_bitmap_node") && Py_SIZE(node) == 2) {
                        auto* pairs =
                          reinterpret_cast<PyObject**>(reinterpret_cast<char*>(node) + ECHION_HAMT_BITMAP_ARRAY_OFFSET);
                        child = pairs[0] == nullptr ? pairs[1] : nullptr;
                    }
                    if (!has_type_name(child, "hamt_collision_node") || Py_SIZE(child) != 4) {
                        error = "no HAMT collision node";
                    } else {
                        layout.collision_type = type_address(child);
                        if (!reader_finds(layout, collision_ctx.get(), first.get(), values[0]) ||
                            !reader_finds(layout, collision_ctx.get(), second.get(), values[1])) {
                            error = "HAMT collision lookup failed";
                        }
                    }
                }
            }
        }
    }

    if (error == nullptr) {
        for (int i = 0; i < count; ++i) {
            if (!reader_finds(layout, ctx.get(), vars[i], values[i])) {
                error = "HAMT lookup failed";
                break;
            }
        }
    }

    for (int i = 0; i < count; ++i) {
        Py_XDECREF(vars[i]);
        Py_XDECREF(values[i]);
    }
    return error;
}

namespace {

bool
fits(uint32_t offset, uint32_t size, uint32_t field, uint32_t field_size)
{
    return field >= offset && field + field_size <= offset + size;
}

// Checks the tracer's layout capsule and copies what the reader needs.
const char*
load_tracer_layout(ContextReaderLayout& layout, const dd_profiler_layout_t*& tracer)
{
    auto* descriptor = static_cast<const dd_profiler_layout_t*>(PyCapsule_Import(DD_PROFILER_LAYOUT_CAPSULE, 0));
    if (descriptor == nullptr) {
        return "no tracer layout";
    }
    if (descriptor->version != DD_PROFILER_LAYOUT_VERSION || descriptor->size != sizeof(dd_profiler_layout_t)) {
        return "unsupported tracer layout version";
    }

    const dd_span_offsets_t& span = descriptor->span;
    const dd_context_offsets_t& context = descriptor->context;
    const uint32_t span_end = span.copy_offset + span.copy_size;
    const uint32_t context_end = context.copy_offset + context.copy_size;
    if (span_end > CONTEXT_READER_MAX_OBJECT_COPY || context_end > CONTEXT_READER_MAX_OBJECT_COPY ||
        span.copy_offset < sizeof(PyObject) || context.copy_offset < sizeof(PyObject)) {
        return "tracer layout out of range";
    }
    const bool span_fields = fits(span.copy_offset, span.copy_size, span.span_id, 8) &&
                             fits(span.copy_offset, span.copy_size, span.parent_id, 8) &&
                             fits(span.copy_offset, span.copy_size, span.parent, sizeof(void*)) &&
                             fits(span.copy_offset, span.copy_size, span.local_root, sizeof(void*)) &&
                             fits(span.copy_offset, span.copy_size, span.parent_context, sizeof(void*)) &&
                             fits(span.copy_offset, span.copy_size, span.span_type_id, 4) &&
                             fits(span.copy_offset, span.copy_size, span.finished, 1);
    const bool context_fields = fits(context.copy_offset, context.copy_size, context.span_id, 8) &&
                                fits(context.copy_offset, context.copy_size, context.local_root_span_id, 8) &&
                                fits(context.copy_offset, context.copy_size, context.span_type_id, 4) &&
                                fits(context.copy_offset, context.copy_size, context.reactivate, 1);
    if (!span_fields || !context_fields) {
        return "tracer layout out of range";
    }

    auto* contextvar = static_cast<PyObject*>(const_cast<void*>(descriptor->contextvar));
    if (contextvar == nullptr || !PyContextVar_CheckExact(contextvar) ||
        descriptor->contextvar_hamt_hash != hamt_hash(PyObject_Hash(contextvar))) {
        return "unexpected tracer contextvar";
    }

    layout.key = address_of(contextvar);
    layout.key_hash = descriptor->contextvar_hamt_hash;
    layout.span_data_type = address_of(descriptor->span_data_type);
    layout.span_type = &descriptor->span_type;
    layout.tracer_context_type = address_of(descriptor->context_type);
    layout.span = span;
    layout.context = context;

    // A value of unknown type takes one copy when the bytes fit in both a span and a Context.
    const uint32_t value_end = std::max(span_end, context_end);
    const auto* span_data_type = static_cast<const PyTypeObject*>(descriptor->span_data_type);
    const auto* context_type = static_cast<const PyTypeObject*>(descriptor->context_type);
    layout.value_copy_size =
      value_end <= std::min(span_data_type->tp_basicsize, context_type->tp_basicsize) ? value_end : 0;

    tracer = descriptor;
    return nullptr;
}

} // namespace

bool
EchionCopy::operator()(uintptr_t address, void* buffer, size_t size) const
{
    return copy_generic(address, buffer, static_cast<ssize_t>(size)) == 0;
}

const char*
context_read_status_name(ContextReadStatus status)
{
    switch (status) {
        case ContextReadStatus::Ok:
            return "ok";
        case ContextReadStatus::NoLabels:
            return "no_labels";
        case ContextReadStatus::Disabled:
            return "disabled";
        case ContextReadStatus::CopyFailed:
            return "copy_failed";
        case ContextReadStatus::CopyBudget:
            return "copy_budget";
        case ContextReadStatus::BadContextType:
            return "bad_context_type";
        case ContextReadStatus::BadHamtType:
            return "bad_hamt_type";
        case ContextReadStatus::BadNode:
            return "bad_node";
        case ContextReadStatus::TooDeep:
            return "too_deep";
        case ContextReadStatus::CollisionTooLarge:
            return "collision_too_large";
        case ContextReadStatus::BadValueType:
            return "bad_value_type";
        case ContextReadStatus::HopLimit:
            return "hop_limit";
        case ContextReadStatus::Count:
            break;
    }
    return "unknown";
}

bool
init_context_reader()
{
    if (g_reader_initialized) {
        return g_reader.enabled.load(std::memory_order_acquire);
    }
    g_reader_initialized = true;

    ContextReaderLayout layout;
    const dd_profiler_layout_t* tracer = nullptr;
    const char* error = load_tracer_layout(layout, tracer);
    if (error == nullptr) {
        error = calibrate_context_layout(layout);
    }

    // Check the tracer's key and hash on a Context that holds it.
    if (error == nullptr) {
        Ref ctx(PyContext_New());
        Ref sentinel(PyLong_FromLong(1000001));
        auto* contextvar = static_cast<PyObject*>(const_cast<void*>(tracer->contextvar));
        EchionCopy copy;
        ContextLookup<EchionCopy> lookup(layout, copy);
        uintptr_t found = 0;
        if (ctx.get() == nullptr || sentinel.get() == nullptr ||
            !set_in_context(ctx.get(), contextvar, sentinel.get()) ||
            lookup.find_value(address_of(ctx.get()), found) != ContextReadStatus::Ok ||
            found != address_of(sentinel.get())) {
            error = "tracer contextvar lookup failed";
        }
    }

    if (PyErr_Occurred()) {
        PyErr_Clear();
    }
    if (error != nullptr) {
        g_reader.error = error;
        return false;
    }

    g_reader.layout = layout;
    g_reader.tracer = tracer;
    g_reader.error = nullptr;
    // Published before the sampler starts; the release pairs with readers that check enabled.
    g_reader.enabled.store(true, std::memory_order_release);
    return true;
}

const ContextReader&
context_reader()
{
    return g_reader;
}

ContextReadStatus
read_context_labels(uintptr_t ctx, ContextLabels& labels, uintptr_t* resolved)
{
    if (!g_reader.enabled.load(std::memory_order_acquire)) {
        return ContextReadStatus::Disabled;
    }
    EchionCopy copy;
    ContextLookup<EchionCopy> lookup(g_reader.layout, copy);
    const ContextReadStatus status = lookup.read(ctx, labels, resolved);
    count_context_read(status);
    return status;
}

std::optional<ContextLabels>
read_labels(PyContext* ctx)
{
    ContextLabels labels;
    if (read_context_labels(address_of(ctx), labels) != ContextReadStatus::Ok) {
        return std::nullopt;
    }
    return labels;
}

std::string_view
span_type_name(uint32_t span_type_id)
{
    if (span_type_id == DD_SPAN_TYPE_NONE || !g_reader.enabled.load(std::memory_order_acquire)) {
        return {};
    }
    const dd_span_type_table_t& table = g_reader.tracer->span_types;
    if (span_type_id == table.unavailable_id || span_type_id >= __atomic_load_n(table.count, __ATOMIC_ACQUIRE)) {
        return {};
    }
    const dd_span_type_entry_t& entry = table.entries[span_type_id];
    return { entry.data, entry.len };
}

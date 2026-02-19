#include <echion/frame.h>

#include <echion/echion_sampler.h>
#include <echion/errors.h>

#if PY_VERSION_HEX >= 0x030b0000
#include <cstddef>
#include <internal/pycore_code.h>
#include <internal/pycore_frame.h>

#if PY_VERSION_HEX >= 0x030e0000
#include <internal/pycore_interpframe_structs.h>
#include <internal/pycore_stackref.h>
#endif // PY_VERSION_HEX >= 0x030e0000
#endif // PY_VERSION_HEX >= 0x030b0000

#if PY_VERSION_HEX >= 0x030b0000
#if PY_VERSION_HEX >= 0x030d0000
#include <opcode.h>
#if PY_VERSION_HEX < 0x030e0000
// Python 3.13: _PyOpcode_Caches and _PyOpcode_Deopt are in pycore_opcode_metadata.h.
// Define NEED_OPCODE_METADATA to get the actual array definitions (not just extern decls)
// so we can link them into our extension module without depending on unexported CPython symbols.
#define NEED_OPCODE_METADATA
#include <internal/pycore_opcode_metadata.h>
#undef NEED_OPCODE_METADATA
#endif
#else
#include <internal/pycore_opcode.h>
#endif
#else
// Python < 3.11
#include <opcode.h>
#endif

// ----------------------------------------------------------------------------
// Check if an opcode is a CALL to a C/builtin function
// In Python 3.11+, opcodes are specialized at runtime, so we check for
// specialized PRECALL_BUILTIN_* variants that indicate a C function call
static inline bool
is_call_opcode([[maybe_unused]] uint8_t opcode)
{
#if PY_VERSION_HEX >= 0x030d0000
    // Python 3.13+: Check for call opcodes that may invoke C/native code.
    // We include the generic CALL opcode (used before specialization) and rely on
    // the stack.empty() check in unwind_frame to filter out calls to Python functions
    // (which push their own frame onto the stack).
    return opcode == CALL || opcode == CALL_FUNCTION_EX || opcode == CALL_KW ||
           // Specialized C/builtin call opcodes
           opcode == CALL_BUILTIN_CLASS || opcode == CALL_BUILTIN_FAST || opcode == CALL_BUILTIN_FAST_WITH_KEYWORDS ||
           opcode == CALL_BUILTIN_O || opcode == CALL_NON_PY_GENERAL ||
           // C method descriptor opcodes
           opcode == CALL_METHOD_DESCRIPTOR_O || opcode == CALL_METHOD_DESCRIPTOR_FAST ||
           opcode == CALL_METHOD_DESCRIPTOR_FAST_WITH_KEYWORDS || opcode == CALL_METHOD_DESCRIPTOR_NOARGS ||
           // Built-in function specializations
           opcode == CALL_ISINSTANCE || opcode == CALL_LEN || opcode == CALL_LIST_APPEND || opcode == CALL_STR_1 ||
           opcode == CALL_TUPLE_1 ||
           opcode == CALL_TYPE_1
#if PY_VERSION_HEX >= 0x030e0000
           // Python 3.14 added CALL_KW specializations
           || opcode == CALL_KW_NON_PY
#endif
      ;
#elif PY_VERSION_HEX >= 0x030c0000
    // Python 3.12: CALL is specialized but no PRECALL
    return opcode == CALL || opcode == CALL_FUNCTION_EX || opcode == CALL_BUILTIN_CLASS ||
           opcode == CALL_BUILTIN_FAST_WITH_KEYWORDS || opcode == CALL_NO_KW_BUILTIN_FAST ||
           opcode == CALL_NO_KW_BUILTIN_O || opcode == CALL_NO_KW_ISINSTANCE || opcode == CALL_NO_KW_LEN ||
           opcode == CALL_NO_KW_LIST_APPEND || opcode == CALL_NO_KW_STR_1 || opcode == CALL_NO_KW_TUPLE_1 ||
           opcode == CALL_NO_KW_TYPE_1;
#elif PY_VERSION_HEX >= 0x030b0000
    // Python 3.11: Check specialized PRECALL_BUILTIN_* variants and CALL
    return opcode == CALL || opcode == PRECALL_BUILTIN_CLASS || opcode == PRECALL_BUILTIN_FAST_WITH_KEYWORDS ||
           opcode == PRECALL_NO_KW_BUILTIN_FAST || opcode == PRECALL_NO_KW_BUILTIN_O ||
           opcode == PRECALL_NO_KW_ISINSTANCE || opcode == PRECALL_NO_KW_LEN || opcode == PRECALL_NO_KW_LIST_APPEND ||
           opcode == PRECALL_NO_KW_STR_1 || opcode == PRECALL_NO_KW_TUPLE_1 || opcode == PRECALL_NO_KW_TYPE_1 ||
           opcode == CALL_FUNCTION_EX;
#else
    // Python 3.10 and earlier
    return opcode == CALL_FUNCTION || opcode == CALL_FUNCTION_KW || opcode == CALL_FUNCTION_EX || opcode == CALL_METHOD;
#endif
}

// ----------------------------------------------------------------------------
#if PY_VERSION_HEX >= 0x030b0000
// Check if an opcode is a LOAD_ATTR/LOAD_METHOD (preferred for method names)
static inline bool
is_load_attr_opcode(uint8_t opcode)
{
    if (opcode == LOAD_ATTR)
        return true;
#if PY_VERSION_HEX < 0x030c0000
    // Python 3.11 specialized LOAD_ATTR variants
    if (opcode == LOAD_ATTR_ADAPTIVE || opcode == LOAD_ATTR_INSTANCE_VALUE || opcode == LOAD_ATTR_MODULE ||
        opcode == LOAD_ATTR_SLOT || opcode == LOAD_ATTR_WITH_HINT || opcode == LOAD_METHOD ||
        opcode == LOAD_METHOD_ADAPTIVE || opcode == LOAD_METHOD_CLASS || opcode == LOAD_METHOD_MODULE ||
        opcode == LOAD_METHOD_NO_DICT || opcode == LOAD_METHOD_WITH_DICT || opcode == LOAD_METHOD_WITH_VALUES)
        return true;
#else
    // Python 3.12+ specialized LOAD_ATTR variants (LOAD_METHOD merged into LOAD_ATTR)
    if (opcode == LOAD_ATTR_CLASS || opcode == LOAD_ATTR_GETATTRIBUTE_OVERRIDDEN ||
        opcode == LOAD_ATTR_INSTANCE_VALUE || opcode == LOAD_ATTR_MODULE || opcode == LOAD_ATTR_PROPERTY ||
        opcode == LOAD_ATTR_SLOT || opcode == LOAD_ATTR_WITH_HINT || opcode == LOAD_ATTR_METHOD_LAZY_DICT ||
        opcode == LOAD_ATTR_METHOD_NO_DICT || opcode == LOAD_ATTR_METHOD_WITH_VALUES)
        return true;
#endif
    return false;
}

// Check if an opcode is a LOAD_GLOBAL/LOAD_NAME (fallback for function names)
static inline bool
is_load_global_opcode(uint8_t opcode)
{
#if PY_VERSION_HEX >= 0x030c0000
    return opcode == LOAD_GLOBAL || opcode == LOAD_NAME || opcode == LOAD_GLOBAL_BUILTIN ||
           opcode == LOAD_GLOBAL_MODULE;
#elif PY_VERSION_HEX >= 0x030b0000
    return opcode == LOAD_GLOBAL || opcode == LOAD_NAME || opcode == LOAD_GLOBAL_ADAPTIVE ||
           opcode == LOAD_GLOBAL_BUILTIN || opcode == LOAD_GLOBAL_MODULE;
#else
    return opcode == LOAD_GLOBAL || opcode == LOAD_NAME;
#endif
}

// Check if a LOAD_ATTR variant is a method load (pushes 2 values: self + method)
[[maybe_unused]] static inline bool
is_load_method_variant([[maybe_unused]] uint8_t opcode, [[maybe_unused]] uint8_t arg)
{
#if PY_VERSION_HEX >= 0x030c0000
    // In Python 3.12+, LOAD_METHOD is merged into LOAD_ATTR with arg & 1 == 1
    return (arg & 1) != 0;
#else
    // In Python 3.11, LOAD_METHOD is a separate opcode family
    return opcode == LOAD_METHOD || opcode == LOAD_METHOD_ADAPTIVE || opcode == LOAD_METHOD_CLASS ||
           opcode == LOAD_METHOD_MODULE || opcode == LOAD_METHOD_NO_DICT || opcode == LOAD_METHOD_WITH_DICT ||
           opcode == LOAD_METHOD_WITH_VALUES;
#endif
}

#if PY_VERSION_HEX >= 0x030d0000
// Check if an opcode is a CALL_KW variant (consumes an extra kwnames tuple)
[[maybe_unused]] static inline bool
is_call_kw_opcode(uint8_t opcode)
{
    return opcode == CALL_KW
#if PY_VERSION_HEX >= 0x030e0000
           || opcode == CALL_KW_NON_PY || opcode == CALL_KW_PY || opcode == CALL_KW_BOUND_METHOD
#endif
      ;
}
#endif // PY_VERSION_HEX >= 0x030d0000

#if PY_VERSION_HEX >= 0x030e0000
// Superinstructions that push 2 values onto the stack
[[maybe_unused]] static inline bool
is_double_load_opcode(uint8_t opcode)
{
    return opcode == LOAD_FAST_LOAD_FAST || opcode == LOAD_FAST_BORROW_LOAD_FAST_BORROW;
}

// Check if an opcode is a BINARY_OP variant (consumes 2, produces 1)
static inline bool
is_binary_op_opcode(uint8_t opcode)
{
    return opcode == BINARY_OP || opcode == BINARY_OP_INPLACE_ADD_UNICODE || opcode == BINARY_OP_ADD_FLOAT ||
           opcode == BINARY_OP_ADD_INT || opcode == BINARY_OP_ADD_UNICODE || opcode == BINARY_OP_EXTEND ||
           opcode == BINARY_OP_MULTIPLY_FLOAT || opcode == BINARY_OP_MULTIPLY_INT || opcode == BINARY_OP_SUBSCR_DICT ||
           opcode == BINARY_OP_SUBSCR_GETITEM || opcode == BINARY_OP_SUBSCR_LIST_INT ||
           opcode == BINARY_OP_SUBSCR_LIST_SLICE || opcode == BINARY_OP_SUBSCR_STR_INT ||
           opcode == BINARY_OP_SUBSCR_TUPLE_INT || opcode == BINARY_OP_SUBTRACT_FLOAT ||
           opcode == BINARY_OP_SUBTRACT_INT;
}

// Number of inline CACHE entries following each opcode in Python 3.14.
// Specialized opcodes inherit the cache count of their base opcode.
// Used for forward-scanning bytecode to identify instruction boundaries.
// clang-format off
static inline int
opcode_num_caches(uint8_t opcode)
{
    switch (opcode) {
    // BINARY_OP family: 5 caches
    case BINARY_OP: case BINARY_OP_INPLACE_ADD_UNICODE:
    case BINARY_OP_ADD_FLOAT: case BINARY_OP_ADD_INT: case BINARY_OP_ADD_UNICODE:
    case BINARY_OP_EXTEND:
    case BINARY_OP_MULTIPLY_FLOAT: case BINARY_OP_MULTIPLY_INT:
    case BINARY_OP_SUBSCR_DICT: case BINARY_OP_SUBSCR_GETITEM:
    case BINARY_OP_SUBSCR_LIST_INT: case BINARY_OP_SUBSCR_LIST_SLICE:
    case BINARY_OP_SUBSCR_STR_INT: case BINARY_OP_SUBSCR_TUPLE_INT:
    case BINARY_OP_SUBTRACT_FLOAT: case BINARY_OP_SUBTRACT_INT:
        return 5;
    // LOAD_ATTR family: 9 caches
    case LOAD_ATTR:
    case LOAD_ATTR_CLASS: case LOAD_ATTR_CLASS_WITH_METACLASS_CHECK:
    case LOAD_ATTR_GETATTRIBUTE_OVERRIDDEN: case LOAD_ATTR_INSTANCE_VALUE:
    case LOAD_ATTR_METHOD_LAZY_DICT: case LOAD_ATTR_METHOD_NO_DICT: case LOAD_ATTR_METHOD_WITH_VALUES:
    case LOAD_ATTR_MODULE:
    case LOAD_ATTR_NONDESCRIPTOR_NO_DICT: case LOAD_ATTR_NONDESCRIPTOR_WITH_VALUES:
    case LOAD_ATTR_PROPERTY: case LOAD_ATTR_SLOT: case LOAD_ATTR_WITH_HINT:
        return 9;
    // CALL family: 3 caches
    case CALL:
    case CALL_ALLOC_AND_ENTER_INIT: case CALL_BOUND_METHOD_EXACT_ARGS:
    case CALL_BOUND_METHOD_GENERAL:
    case CALL_BUILTIN_CLASS: case CALL_BUILTIN_FAST: case CALL_BUILTIN_FAST_WITH_KEYWORDS: case CALL_BUILTIN_O:
    case CALL_ISINSTANCE: case CALL_LEN: case CALL_LIST_APPEND:
    case CALL_METHOD_DESCRIPTOR_FAST: case CALL_METHOD_DESCRIPTOR_FAST_WITH_KEYWORDS:
    case CALL_METHOD_DESCRIPTOR_NOARGS: case CALL_METHOD_DESCRIPTOR_O:
    case CALL_NON_PY_GENERAL: case CALL_PY_EXACT_ARGS: case CALL_PY_GENERAL:
    case CALL_STR_1: case CALL_TUPLE_1: case CALL_TYPE_1:
        return 3;
    // CALL_KW family: 3 caches
    case CALL_KW:
    case CALL_KW_BOUND_METHOD: case CALL_KW_NON_PY: case CALL_KW_PY:
        return 3;
    // STORE_ATTR family: 4 caches
    case STORE_ATTR:
    case STORE_ATTR_INSTANCE_VALUE: case STORE_ATTR_SLOT: case STORE_ATTR_WITH_HINT:
        return 4;
    // LOAD_GLOBAL family: 4 caches
    case LOAD_GLOBAL:
    case LOAD_GLOBAL_BUILTIN: case LOAD_GLOBAL_MODULE:
        return 4;
    // TO_BOOL family: 3 caches
    case TO_BOOL:
    case TO_BOOL_ALWAYS_TRUE: case TO_BOOL_BOOL: case TO_BOOL_INT:
    case TO_BOOL_LIST: case TO_BOOL_NONE: case TO_BOOL_STR:
        return 3;
    // Families with 1 cache each
    case COMPARE_OP: case COMPARE_OP_FLOAT: case COMPARE_OP_INT: case COMPARE_OP_STR:
    case CONTAINS_OP: case CONTAINS_OP_DICT: case CONTAINS_OP_SET:
    case FOR_ITER: case FOR_ITER_GEN: case FOR_ITER_LIST: case FOR_ITER_RANGE: case FOR_ITER_TUPLE:
    case JUMP_BACKWARD: case JUMP_BACKWARD_JIT: case JUMP_BACKWARD_NO_JIT:
    case LOAD_SUPER_ATTR: case LOAD_SUPER_ATTR_ATTR: case LOAD_SUPER_ATTR_METHOD:
    case SEND: case SEND_GEN:
    case STORE_SUBSCR: case STORE_SUBSCR_DICT: case STORE_SUBSCR_LIST_INT:
    case UNPACK_SEQUENCE: case UNPACK_SEQUENCE_LIST: case UNPACK_SEQUENCE_TUPLE: case UNPACK_SEQUENCE_TWO_TUPLE:
    case POP_JUMP_IF_FALSE: case POP_JUMP_IF_TRUE:
    case POP_JUMP_IF_NONE: case POP_JUMP_IF_NOT_NONE:
        return 1;
    default:
        return 0;
    }
}
// clang-format on
#endif // PY_VERSION_HEX >= 0x030e0000

#endif // PY_VERSION_HEX >= 0x030b0000

// ----------------------------------------------------------------------------
#if PY_VERSION_HEX >= 0x030b0000
// Cache key for lookup_name_from_code: (code_addr, name_idx) -> name_key
struct NameLookupKey
{
    uintptr_t code_addr;
    int name_idx;

    bool operator==(const NameLookupKey& other) const
    {
        return code_addr == other.code_addr && name_idx == other.name_idx;
    }
};

namespace std {
template<>
struct hash<NameLookupKey>
{
    size_t operator()(const NameLookupKey& key) const
    {
        return hash<uintptr_t>()(key.code_addr) ^ (hash<int>()(key.name_idx) << 1);
    }
};
}

// LRU cache for name lookups: maps (code_addr, name_idx) -> StringTable::Key
static LRUCache<NameLookupKey, StringTable::Key>* name_lookup_cache = nullptr;

static void
init_name_lookup_cache()
{
    if (name_lookup_cache == nullptr) {
        name_lookup_cache = new LRUCache<NameLookupKey, StringTable::Key>(512);
    }
}

// ----------------------------------------------------------------------------
// Helper to look up a name from co_names by index
static inline StringTable::Key
lookup_name_from_code(EchionSampler& echion, PyCodeObject* code_addr, int name_idx)
{
    init_name_lookup_cache();

    NameLookupKey cache_key{ reinterpret_cast<uintptr_t>(code_addr), name_idx };
    auto cached_result = name_lookup_cache->lookup(cache_key);
    if (cached_result) {
        return cached_result->get();
    }

    PyCodeObject code;
    if (copy_type(code_addr, code))
        return 0;

    PyTupleObject names_tuple;
    if (copy_type(code.co_names, names_tuple))
        return 0;

    if (name_idx < 0 || name_idx >= static_cast<int>(names_tuple.ob_base.ob_size))
        return 0;

    PyObject* name_obj_ptr;
    auto item_addr = reinterpret_cast<PyObject**>(reinterpret_cast<uintptr_t>(code.co_names) +
                                                  offsetof(PyTupleObject, ob_item) + name_idx * sizeof(PyObject*));
    if (copy_type(item_addr, name_obj_ptr))
        return 0;

    auto maybe_name = echion.string_table().key(name_obj_ptr, StringTag::FuncName);
    StringTable::Key result = maybe_name ? *maybe_name : 0;

    name_lookup_cache->store(cache_key, std::make_unique<StringTable::Key>(result));

    return result;
}
#endif // PY_VERSION_HEX >= 0x030b0000

// ----------------------------------------------------------------------------
#if PY_VERSION_HEX >= 0x030b0000
// Cache key for extract_callable_name: (code_addr, instr_ptr) -> name_key
struct CallableNameKey
{
    uintptr_t code_addr;
    uintptr_t instr_ptr;

    bool operator==(const CallableNameKey& other) const
    {
        return code_addr == other.code_addr && instr_ptr == other.instr_ptr;
    }
};

namespace std {
template<>
struct hash<CallableNameKey>
{
    size_t operator()(const CallableNameKey& key) const
    {
        return hash<uintptr_t>()(key.code_addr) ^ (hash<uintptr_t>()(key.instr_ptr) << 1);
    }
};
}

static LRUCache<CallableNameKey, StringTable::Key>* callable_name_cache = nullptr;

static void
init_callable_name_cache()
{
    if (callable_name_cache == nullptr) {
        callable_name_cache = new LRUCache<CallableNameKey, StringTable::Key>(512);
    }
}

// ----------------------------------------------------------------------------
// Extract the callable name from bytecode by scanning backwards from the current CALL instruction.
//
// Python 3.14+ uses a forward scan to identify instruction boundaries (CACHE entries in the
// adaptive bytecode can contain arbitrary data that looks like valid opcodes, making backward
// scanning unreliable). Then does stack-depth tracking to correctly skip nested call expressions.
//
// Python 3.11-3.13 uses a simpler two-pass backward scan (find nearest LOAD_ATTR, then LOAD_GLOBAL)
// which works because CACHE data rarely matches LOAD_ATTR/LOAD_GLOBAL opcode values.
static inline StringTable::Key
extract_callable_name(EchionSampler& echion, _Py_CODEUNIT* instr_ptr, PyCodeObject* code_addr)
{
    init_callable_name_cache();

    CallableNameKey cache_key{ reinterpret_cast<uintptr_t>(code_addr), reinterpret_cast<uintptr_t>(instr_ptr) };
    auto cached_result = callable_name_cache->lookup(cache_key);
    if (cached_result) {
        return cached_result->get();
    }

    auto code_start = reinterpret_cast<_Py_CODEUNIT*>(reinterpret_cast<uintptr_t>(code_addr) +
                                                      offsetof(PyCodeObject, co_code_adaptive));

    if (instr_ptr <= code_start)
        return 0;

    StringTable::Key result = 0;

#if PY_VERSION_HEX >= 0x030e0000
    // Python 3.14+: Forward scan to find instruction boundaries, then backward depth tracking.
    // This correctly handles nested calls like `f(g(x), h(y))` where we want "f", not "h".
    int total_units = static_cast<int>(instr_ptr - code_start);
    if (total_units <= 0 || total_units > 4096)
        goto done;

    {
        _Py_CODEUNIT stack_buf[256];
        _Py_CODEUNIT* bytecode = total_units <= 256 ? stack_buf : new _Py_CODEUNIT[total_units];

        if (copy_generic(code_start, bytecode, total_units * sizeof(_Py_CODEUNIT))) {
            if (bytecode != stack_buf)
                delete[] bytecode;
            goto done;
        }

        // Forward scan: walk through bytecode from the start, using opcode_num_caches()
        // to skip CACHE entries. Record the last MAX_INSTRS instruction offsets.
        constexpr int MAX_INSTRS = 64;
        int instr_offsets[MAX_INSTRS];
        int num_instrs = 0;

        for (int pos = 0; pos < total_units;) {
            instr_offsets[num_instrs % MAX_INSTRS] = pos;
            num_instrs++;
            pos += 1 + opcode_num_caches(_Py_OPCODE(bytecode[pos]));
        }

        // Read the CALL instruction to get its oparg
        _Py_CODEUNIT call_instr;
        if (copy_type(instr_ptr, call_instr)) {
            if (bytecode != stack_buf)
                delete[] bytecode;
            goto done;
        }
        uint8_t call_opcode = _Py_OPCODE(call_instr);
        int call_oparg = _Py_OPARG(call_instr);

        // Depth = number of stack values to skip (NULL/self + args [+ kwnames])
        int depth = call_oparg + 1;
        if (is_call_kw_opcode(call_opcode))
            depth += 1;

        // Walk backward through real instruction offsets only
        int start_idx = num_instrs > MAX_INSTRS ? num_instrs - MAX_INSTRS : 0;
        for (int i = num_instrs - 1; i >= start_idx && depth >= 0; --i) {
            int pos = instr_offsets[i % MAX_INSTRS];
            uint8_t opcode = _Py_OPCODE(bytecode[pos]);
            uint8_t arg = _Py_OPARG(bytecode[pos]);

            int produced = 1;
            int consumed = 0;

            if (is_call_opcode(opcode)) {
                if (opcode == CALL_FUNCTION_EX)
                    break;
                consumed = arg + 2;
                // CALL_KW variants consume an extra kwnames tuple
                if (is_call_kw_opcode(opcode))
                    consumed = arg + 3;
            } else if (is_load_attr_opcode(opcode)) {
                consumed = 1;
                if (is_load_method_variant(opcode, arg))
                    produced = 2;
            } else if (is_load_global_opcode(opcode)) {
                if (arg & 1)
                    produced = 2;
            } else if (is_double_load_opcode(opcode)) {
                produced = 2;
            } else if (is_binary_op_opcode(opcode)) {
                consumed = 2;
            } else if (opcode == POP_TOP) {
                produced = 0;
                consumed = 1;
            }

            depth -= produced;

            if (depth < 0) {
                if (is_load_attr_opcode(opcode)) {
                    int name_idx = arg >> 1;
                    result = lookup_name_from_code(echion, code_addr, name_idx);
                } else if (is_load_global_opcode(opcode)) {
                    int name_idx = arg >> 1;
                    result = lookup_name_from_code(echion, code_addr, name_idx);
                }
                break;
            }

            depth += consumed;
        }

        if (bytecode != stack_buf)
            delete[] bytecode;
    }
#elif PY_VERSION_HEX >= 0x030d0000
    // Python 3.13: Forward scan to find instruction boundaries, then backward depth tracking.
    // Uses _PyOpcode_Caches and _PyOpcode_Deopt arrays (compiled in via NEED_OPCODE_METADATA)
    // to correctly handle CACHE entries in adaptive bytecode.
    int total_units = static_cast<int>(instr_ptr - code_start);
    if (total_units <= 0 || total_units > 4096)
        goto done;

    {
        _Py_CODEUNIT stack_buf[256];
        _Py_CODEUNIT* bytecode = total_units <= 256 ? stack_buf : new _Py_CODEUNIT[total_units];

        if (copy_generic(code_start, bytecode, total_units * sizeof(_Py_CODEUNIT))) {
            if (bytecode != stack_buf)
                delete[] bytecode;
            goto done;
        }

        // Forward scan: walk through bytecode from the start, using _PyOpcode_Caches
        // to skip CACHE entries. Record the last MAX_INSTRS instruction offsets.
        constexpr int MAX_INSTRS = 64;
        int instr_offsets[MAX_INSTRS];
        int num_instrs = 0;

        for (int pos = 0; pos < total_units;) {
            instr_offsets[num_instrs % MAX_INSTRS] = pos;
            num_instrs++;
            uint8_t op = _Py_OPCODE(bytecode[pos]);
            pos += 1 + _PyOpcode_Caches[_PyOpcode_Deopt[op]];
        }

        // Read the CALL instruction to get its oparg
        _Py_CODEUNIT call_instr;
        if (copy_type(instr_ptr, call_instr)) {
            if (bytecode != stack_buf)
                delete[] bytecode;
            goto done;
        }
        uint8_t call_opcode = _Py_OPCODE(call_instr);
        int call_oparg = _Py_OPARG(call_instr);

        // Depth = number of stack values to skip (NULL/self + args [+ kwnames])
        int depth = call_oparg + 1;
        if (is_call_kw_opcode(call_opcode))
            depth += 1;

        // Walk backward through real instruction offsets only
        int start_idx = num_instrs > MAX_INSTRS ? num_instrs - MAX_INSTRS : 0;
        for (int i = num_instrs - 1; i >= start_idx && depth >= 0; --i) {
            int pos = instr_offsets[i % MAX_INSTRS];
            uint8_t opcode = _Py_OPCODE(bytecode[pos]);
            uint8_t arg = _Py_OPARG(bytecode[pos]);

            int produced = 1;
            int consumed = 0;

            if (is_call_opcode(opcode)) {
                if (opcode == CALL_FUNCTION_EX)
                    break;
                consumed = arg + 2;
                // CALL_KW variants consume an extra kwnames tuple
                if (is_call_kw_opcode(opcode))
                    consumed = arg + 3;
            } else if (is_load_attr_opcode(opcode)) {
                consumed = 1;
                if (is_load_method_variant(opcode, arg))
                    produced = 2;
            } else if (is_load_global_opcode(opcode)) {
                if (arg & 1)
                    produced = 2;
            } else if (_PyOpcode_Deopt[opcode] == BINARY_OP
#ifdef BINARY_SUBSCR
                       || _PyOpcode_Deopt[opcode] == BINARY_SUBSCR
#endif
            ) {
                consumed = 2;
#ifdef LOAD_FAST_LOAD_FAST
            } else if (opcode == LOAD_FAST_LOAD_FAST) {
                produced = 2;
#endif
            } else if (opcode == POP_TOP) {
                produced = 0;
                consumed = 1;
            }

            depth -= produced;

            if (depth < 0) {
                if (is_load_attr_opcode(opcode)) {
                    int name_idx = arg >> 1;
                    result = lookup_name_from_code(echion, code_addr, name_idx);
                } else if (is_load_global_opcode(opcode)) {
                    int name_idx = arg >> 1;
                    result = lookup_name_from_code(echion, code_addr, name_idx);
                }
                break;
            }

            depth += consumed;
        }

        if (bytecode != stack_buf)
            delete[] bytecode;
    }
#else
    // Python 3.11-3.12: Simple backward scan. CACHE entries in the adaptive bytecode
    // can have non-zero opcode bytes, but they rarely match LOAD_ATTR or LOAD_GLOBAL
    // opcode values, so the simple scan works in practice.
    {
        constexpr int MAX_SCAN = 32;
        _Py_CODEUNIT bytecode[MAX_SCAN];

        int available = static_cast<int>(instr_ptr - code_start);
        int to_scan = std::min(available, MAX_SCAN);
        if (to_scan <= 0)
            goto done;

        auto scan_start = instr_ptr - to_scan;
        if (copy_generic(scan_start, bytecode, to_scan * sizeof(_Py_CODEUNIT)))
            goto done;

        // First pass: look for LOAD_ATTR/LOAD_METHOD (the method/attribute being called)
        for (int i = to_scan - 1; i >= 0; --i) {
            uint8_t opcode = _Py_OPCODE(bytecode[i]);
            if (is_load_attr_opcode(opcode)) {
                int arg = _Py_OPARG(bytecode[i]);
                int name_idx = arg;
                result = lookup_name_from_code(echion, code_addr, name_idx);
                goto done;
            }
        }

        // Second pass: fall back to LOAD_GLOBAL/LOAD_NAME (for direct function calls)
        for (int i = to_scan - 1; i >= 0; --i) {
            uint8_t opcode = _Py_OPCODE(bytecode[i]);
            if (is_load_global_opcode(opcode)) {
                int name_idx = _Py_OPARG(bytecode[i]) >> 1;
                result = lookup_name_from_code(echion, code_addr, name_idx);
                goto done;
            }
        }
    }
#endif

done:
    callable_name_cache->store(cache_key, std::make_unique<StringTable::Key>(result));
    return result;
}
#endif // PY_VERSION_HEX >= 0x030b0000

// ----------------------------------------------------------------------------
#if PY_VERSION_HEX >= 0x030b0000
static inline int
_read_varint(unsigned char* table, ssize_t size, ssize_t* i)
{
    ssize_t guard = size - 1;
    if (*i >= guard)
        return 0;

    int val = table[++*i] & 63;
    int shift = 0;
    while (table[*i] & 64 && *i < guard) {
        shift += 6;
        val |= (table[++*i] & 63) << shift;
    }
    return val;
}

// ----------------------------------------------------------------------------
static inline int
_read_signed_varint(unsigned char* table, ssize_t size, ssize_t* i)
{
    int val = _read_varint(table, size, i);
    return (val & 1) ? -(val >> 1) : (val >> 1);
}
#endif

// ------------------------------------------------------------------------
Result<Frame::Ptr>
Frame::create(EchionSampler& echion, PyCodeObject* code, int lasti)
{
    auto maybe_filename = echion.string_table().key(code->co_filename, StringTag::FileName);
    if (!maybe_filename) {
        return ErrorKind::FrameError;
    }

#if PY_VERSION_HEX >= 0x030b0000
    auto maybe_name = echion.string_table().key(code->co_qualname, StringTag::FuncName);
#else
    auto maybe_name = echion.string_table().key(code->co_name, StringTag::FuncName);
#endif

    if (!maybe_name) {
        return ErrorKind::FrameError;
    }

    auto frame = std::make_unique<Frame>(*maybe_filename, *maybe_name);
    auto infer_location_success = frame->infer_location(code, lasti);
    if (!infer_location_success) {
        return ErrorKind::LocationError;
    }

    return frame;
}

// ----------------------------------------------------------------------------
Result<void>
Frame::infer_location(PyCodeObject* code_obj, int lasti)
{
    unsigned int lineno = code_obj->co_firstlineno;
    Py_ssize_t len = 0;

#if PY_VERSION_HEX >= 0x030b0000
    auto table = pybytes_to_bytes_and_size(code_obj->co_linetable, &len);
    if (table == nullptr) {
        return ErrorKind::LocationError;
    }

    auto table_data = table.get();

    for (Py_ssize_t i = 0, bc = 0; i < len; i++) {
        bc += (table[i] & 7) + 1;
        int code = (table[i] >> 3) & 15;
        unsigned char next_byte = 0;
        switch (code) {
            case 15:
                break;

            case 14: // Long form
                lineno += _read_signed_varint(table_data, len, &i);

                this->location.line = lineno;
                this->location.line_end = lineno + _read_varint(table_data, len, &i);
                this->location.column = _read_varint(table_data, len, &i);
                this->location.column_end = _read_varint(table_data, len, &i);

                break;

            case 13: // No column data
                lineno += _read_signed_varint(table_data, len, &i);

                this->location.line = lineno;
                this->location.line_end = lineno;
                this->location.column = this->location.column_end = 0;

                break;

            case 12: // New lineno
            case 11:
            case 10:
                if (i >= len - 2) {
                    return ErrorKind::LocationError;
                }

                lineno += code - 10;

                this->location.line = lineno;
                this->location.line_end = lineno;
                this->location.column = 1 + table[++i];
                this->location.column_end = 1 + table[++i];

                break;

            default:
                if (i >= len - 1) {
                    return ErrorKind::LocationError;
                }

                next_byte = table[++i];

                this->location.line = lineno;
                this->location.line_end = lineno;
                this->location.column = 1 + (code << 3) + ((next_byte >> 4) & 7);
                this->location.column_end = this->location.column + (next_byte & 15);
        }

        if (bc > lasti)
            break;
    }

#elif PY_VERSION_HEX >= 0x030a0000
    auto table = pybytes_to_bytes_and_size(code_obj->co_linetable, &len);
    if (table == nullptr) {
        return ErrorKind::LocationError;
    }

    lasti <<= 1;
    for (int i = 0, bc = 0; i < len; i++) {
        int sdelta = table[i++];
        if (sdelta == 0xff)
            break;

        bc += sdelta;

        int ldelta = table[i];
        if (ldelta == 0x80)
            ldelta = 0;
        else if (ldelta > 0x80)
            lineno -= 0x100;

        lineno += ldelta;
        if (bc > lasti)
            break;
    }

#else
    auto table = pybytes_to_bytes_and_size(code_obj->co_lnotab, &len);
    if (table == nullptr) {
        return ErrorKind::LocationError;
    }

    for (int i = 0, bc = 0; i < len; i++) {
        bc += table[i++];
        if (bc > lasti)
            break;

        if (table[i] >= 0x80)
            lineno -= 0x100;

        lineno += table[i];
    }

#endif

    this->location.line = lineno;
    this->location.line_end = lineno;
    this->location.column = 0;
    this->location.column_end = 0;

    return Result<void>::ok();
}

// ------------------------------------------------------------------------
Frame::Key
Frame::key(PyCodeObject* code, int lasti)
{
    return ((static_cast<uintptr_t>(((reinterpret_cast<uintptr_t>(code)))) << 16) | lasti);
}

// ------------------------------------------------------------------------
#if PY_VERSION_HEX >= 0x030b0000
Result<std::reference_wrapper<Frame>>
Frame::read(EchionSampler& echion, _PyInterpreterFrame* frame_addr, _PyInterpreterFrame** prev_addr)
#else
Result<std::reference_wrapper<Frame>>
Frame::read(EchionSampler& echion, PyObject* frame_addr, PyObject** prev_addr)
#endif
{
#if PY_VERSION_HEX >= 0x030b0000
    _PyInterpreterFrame iframe;
    auto resolved_addr =
      stack_chunk ? reinterpret_cast<_PyInterpreterFrame*>(stack_chunk->resolve(frame_addr)) : frame_addr;
    if (resolved_addr != frame_addr) {
        frame_addr = resolved_addr;
    } else {
        if (copy_type(frame_addr, iframe)) {
            return ErrorKind::FrameError;
        }
        frame_addr = &iframe;
    }
    if (frame_addr == NULL) {
        return ErrorKind::FrameError;
    }

#if PY_VERSION_HEX >= 0x030c0000
#if PY_VERSION_HEX >= 0x030e0000
    // Python 3.14 introduced FRAME_OWNED_BY_INTERPRETER, and frames of this
    // type are also ignored by the upstream profiler.
    // See
    // https://github.com/python/cpython/blob/ebf955df7a89ed0c7968f79faec1de49f61ed7cb/Modules/_remote_debugging_module.c#L2134
    if (frame_addr->owner == FRAME_OWNED_BY_CSTACK || frame_addr->owner == FRAME_OWNED_BY_INTERPRETER) {
#else
    if (frame_addr->owner == FRAME_OWNED_BY_CSTACK) {
#endif // PY_VERSION_HEX >= 0x030e0000
        *prev_addr = frame_addr->previous;
        // This is a C frame, we just need to ignore it
        return std::ref(C_FRAME);
    }

    if (frame_addr->owner != FRAME_OWNED_BY_THREAD && frame_addr->owner != FRAME_OWNED_BY_GENERATOR) {
        return ErrorKind::FrameError;
    }
#endif // PY_VERSION_HEX >= 0x030c0000

    // We cannot use _PyInterpreterFrame_LASTI because _PyCode_CODE reads
    // from the code object.
#if PY_VERSION_HEX >= 0x030e0000
    // Per Python 3.14 release notes (gh-123923): f_executable uses a tagged pointer.
    // Profilers must clear the least significant bit to recover the PyObject* pointer.
    PyCodeObject* code_obj = reinterpret_cast<PyCodeObject*>(BITS_TO_PTR_MASKED(frame_addr->f_executable));
    _Py_CODEUNIT* code_units = reinterpret_cast<_Py_CODEUNIT*>(code_obj);
    int instr_offset = static_cast<int>(frame_addr->instr_ptr - 1 - code_units);
    int code_offset = offsetof(PyCodeObject, co_code_adaptive) / sizeof(_Py_CODEUNIT);
    const int lasti = instr_offset - code_offset;
    auto maybe_frame = Frame::get(echion, code_obj, lasti);
    if (!maybe_frame) {
        return ErrorKind::FrameError;
    }

    auto& frame = maybe_frame->get();
#elif PY_VERSION_HEX >= 0x030d0000
    const int lasti =
      (static_cast<int>(
        (frame_addr->instr_ptr - 1 -
         reinterpret_cast<_Py_CODEUNIT*>((reinterpret_cast<PyCodeObject*>(frame_addr->f_executable)))))) -
      offsetof(PyCodeObject, co_code_adaptive) / sizeof(_Py_CODEUNIT);
    auto maybe_frame = Frame::get(echion, reinterpret_cast<PyCodeObject*>(frame_addr->f_executable), lasti);
    if (!maybe_frame) {
        return ErrorKind::FrameError;
    }

    auto& frame = maybe_frame->get();
#else
    const int lasti =
      (static_cast<int>((frame_addr->prev_instr - reinterpret_cast<_Py_CODEUNIT*>((frame_addr->f_code))))) -
      offsetof(PyCodeObject, co_code_adaptive) / sizeof(_Py_CODEUNIT);
    auto maybe_frame = Frame::get(echion, frame_addr->f_code, lasti);
    if (!maybe_frame) {
        return ErrorKind::FrameError;
    }

    auto& frame = maybe_frame->get();
#endif // PY_VERSION_HEX >= 0x030e0000
    if (&frame != &INVALID_FRAME) {
#if PY_VERSION_HEX >= 0x030c0000
        frame.is_entry = (frame_addr->owner == FRAME_OWNED_BY_CSTACK); // Shim frame
#else                                                                  // PY_VERSION_HEX < 0x030c0000
        frame.is_entry = frame_addr->is_entry;
#endif                                                                 // PY_VERSION_HEX >= 0x030c0000

        // Detect if we're paused at a CALL instruction (likely in C code)
        _Py_CODEUNIT instr;
#if PY_VERSION_HEX >= 0x030d0000
        // In 3.13+, instr_ptr points to the current instruction
        if (!copy_type(frame_addr->instr_ptr, instr)) {
            frame.in_c_call = is_call_opcode(_Py_OPCODE(instr));
            if (frame.in_c_call) {
#if PY_VERSION_HEX >= 0x030e0000
                frame.c_call_name =
                  extract_callable_name(echion,
                                        frame_addr->instr_ptr,
                                        reinterpret_cast<PyCodeObject*>(BITS_TO_PTR_MASKED(frame_addr->f_executable)));
#else
                frame.c_call_name = extract_callable_name(
                  echion, frame_addr->instr_ptr, reinterpret_cast<PyCodeObject*>(frame_addr->f_executable));
#endif
            }
        }
#else
        // In 3.11-3.12, prev_instr points to the last executed instruction
        if (!copy_type(frame_addr->prev_instr, instr)) {
            frame.in_c_call = is_call_opcode(_Py_OPCODE(instr));
            if (frame.in_c_call) {
                frame.c_call_name = extract_callable_name(echion, frame_addr->prev_instr, frame_addr->f_code);
            }
        }
#endif
    }
    *prev_addr = &frame == &INVALID_FRAME ? NULL : frame_addr->previous;

#else  // PY_VERSION_HEX < 0x030b0000
    // Unwind the stack from leaf to root and store it in a stack. This way we
    // can print it from root to leaf.
    PyFrameObject py_frame;

    if (copy_type(frame_addr, py_frame)) {
        return ErrorKind::FrameError;
    }

    auto maybe_frame = Frame::get(echion, py_frame.f_code, py_frame.f_lasti);
    if (!maybe_frame) {
        return ErrorKind::FrameError;
    }

    auto& frame = maybe_frame->get();

    // Detect if we're paused at a CALL instruction (likely in C code)
    if (&frame != &INVALID_FRAME && py_frame.f_lasti >= 0) {
        PyCodeObject code;
        if (!copy_type(py_frame.f_code, code)) {
            uint8_t opcode;
            auto bytecode_ptr = reinterpret_cast<uint8_t*>(reinterpret_cast<uintptr_t>(code.co_code) +
                                                           offsetof(PyBytesObject, ob_sval) + py_frame.f_lasti);
            if (!copy_type(bytecode_ptr, opcode)) {
                frame.in_c_call = is_call_opcode(opcode);
            }
        }
    }

    *prev_addr = (&frame == &INVALID_FRAME) ? NULL : reinterpret_cast<PyObject*>(py_frame.f_back);
#endif // PY_VERSION_HEX >= 0x030b0000

    // If the frame is in a C call and we have a callable name, create a synthetic
    // C frame and store it in the cache for later use during stack unwinding.
    if (frame.in_c_call && frame.c_call_name != 0) {
        const auto& c_frame_filename = frame.filename;
        const auto& c_frame_location = frame.location;

        uintptr_t c_frame_key = c_frame_filename;
        c_frame_key = (c_frame_key * 31) + frame.c_call_name;
        c_frame_key = (c_frame_key * 31) + static_cast<uintptr_t>(c_frame_location.line);
        c_frame_key = (c_frame_key * 31) + static_cast<uintptr_t>(c_frame_location.column);

        auto c_frame = std::make_unique<Frame>(c_frame_filename, frame.c_call_name, c_frame_location);
        c_frame->is_c_frame = true;
        c_frame->cache_key = c_frame_key;

        frame.c_frame_key = c_frame_key;
        echion.frame_cache().store(c_frame_key, std::move(c_frame));
    }

    return std::ref(frame);
}

// ----------------------------------------------------------------------------
Result<std::reference_wrapper<Frame>>
Frame::get(EchionSampler& echion, PyCodeObject* code_addr, int lasti)
{
    auto frame_key = Frame::key(code_addr, lasti);

    auto maybe_frame = echion.frame_cache().lookup(frame_key);
    if (maybe_frame) {
        return *maybe_frame;
    }

    PyCodeObject code;
    if (copy_type(code_addr, code)) {
        return std::ref(INVALID_FRAME);
    }

    auto maybe_new_frame = Frame::create(echion, &code, lasti);
    if (!maybe_new_frame) {
        return std::ref(INVALID_FRAME);
    }

    auto new_frame = std::move(*maybe_new_frame);
    new_frame->cache_key = frame_key;
    auto& f = *new_frame;
    echion.frame_cache().store(frame_key, std::move(new_frame));
    return std::ref(f);
}

// ----------------------------------------------------------------------------
Frame&
Frame::get(EchionSampler& echion, StringTable::Key name)
{
    uintptr_t frame_key = static_cast<uintptr_t>(name);

    auto maybe_frame = echion.frame_cache().lookup(frame_key);
    if (maybe_frame) {
        return *maybe_frame;
    }

    auto frame = std::make_unique<Frame>(name);
    frame->cache_key = frame_key;
    auto& f = *frame;
    echion.frame_cache().store(frame_key, std::move(frame));
    return f;
}

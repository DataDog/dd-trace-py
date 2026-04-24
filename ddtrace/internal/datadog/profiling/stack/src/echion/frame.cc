#include <echion/frame.h>

#include <echion/echion_sampler.h>
#include <echion/errors.h>

#include <profiling_helpers/frame_accessors.h>
#include <profiling_helpers/linetable_parser.h>

#if PY_VERSION_HEX >= 0x030b0000
#include <cstddef>
#endif // PY_VERSION_HEX >= 0x030b0000

// ------------------------------------------------------------------------
Result<Frame::Ptr>
Frame::create(EchionSampler& echion, PyCodeObject* code, int lasti)
{
    auto maybe_filename = echion.string_table().key(code->co_filename, StringTag::FileName);
    if (!maybe_filename) {
        return ErrorKind::FrameError;
    }

    auto maybe_name = echion.string_table().key(DataDog::get_code_name(code), StringTag::FuncName);

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
Frame::infer_location(PyCodeObject* code_obj, int instr_offset)
{
    Py_ssize_t len = 0;

#if PY_VERSION_HEX >= 0x030a0000
    auto table = pybytes_to_bytes_and_size(code_obj->co_linetable, &len);
#else
    auto table = pybytes_to_bytes_and_size(code_obj->co_lnotab, &len);
#endif

    if (table == nullptr) {
        return ErrorKind::LocationError;
    }

    this->line = DataDog::parse_linetable(table.get(), len, instr_offset, code_obj->co_firstlineno);

    return Result<void>::ok();
}

// ------------------------------------------------------------------------
Frame::Key
Frame::key(PyCodeObject* code, int lasti, int firstlineno)
{
    // Include co_firstlineno in the key to prevent ABA-problem cache collisions.
    // Python's GC can free a PyCodeObject and allocate a new one at the same address. Without
    // firstlineno, a cached <module> frame could be returned for an unrelated function frame.
    // The original (code_addr << 16) | lasti formula also loses the top 16 bits of code_addr
    // and collides when lasti > 0xFFFF; this hash avoids both issues.
    uintptr_t h = reinterpret_cast<uintptr_t>(code);
    // 2654435761 is the Knuth multiplicative hash constant: floor(2^32 / phi), where phi is the
    // golden ratio. It spreads sequential integers across the full 32-bit range.
    h ^= static_cast<uintptr_t>(static_cast<uint32_t>(lasti)) * 2654435761ULL;
    // 40503 is floor(2^16 / phi), the 16-bit analogue of the Knuth constant above.
    h ^= static_cast<uintptr_t>(static_cast<uint32_t>(firstlineno)) * 40503ULL;
    return h;
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
    // Exhaustive switch on _frameowner so -Wswitch fires if CPython adds a
    // new value and we forget to handle it.  See test_cpython_layout_contracts
    // for static_asserts that verify the enum values match our expectations.
    switch (frame_addr->owner) {
        case FRAME_OWNED_BY_THREAD:
        case FRAME_OWNED_BY_GENERATOR:
            break; // valid live Python frame — proceed with frame reading
        case FRAME_OWNED_BY_FRAME_OBJECT:
            return ErrorKind::FrameError; // frame belongs to a PyFrameObject, not executing
#if PY_VERSION_HEX < 0x030f0000
        case FRAME_OWNED_BY_CSTACK: // C shim frame (removed in 3.15)
#endif
#if PY_VERSION_HEX >= 0x030e0000
        case FRAME_OWNED_BY_INTERPRETER:
#endif
            // C/interpreter-managed frame — skip it and follow the frame chain.
            // FRAME_OWNED_BY_INTERPRETER introduced in 3.14; FRAME_OWNED_BY_CSTACK
            // present in 3.12–3.14, removed in 3.15.
            // See
            // https://github.com/python/cpython/blob/ebf955df7a89ed0c7968f79faec1de49f61ed7cb/Modules/_remote_debugging_module.c#L2134
            *prev_addr = frame_addr->previous;
            return std::ref(C_FRAME);
            // No default — intentional: -Wswitch warns if a new _frameowner value
            // is added to CPython without a corresponding case here.
    }
#endif // PY_VERSION_HEX >= 0x030c0000

        // We cannot use _PyInterpreterFrame_LASTI because _PyCode_CODE reads
        // from the code object, which is a remote address here.  Use offsetof
        // arithmetic instead to avoid dereferencing it.
#if PY_VERSION_HEX >= 0x030d0000
    // DataDog::get_code_from_frame() handles both Python 3.13 (untagged
    // f_executable) and 3.14+ (tagged _PyStackRef f_executable) transparently.
    PyCodeObject* code_obj = DataDog::get_code_from_frame(frame_addr);
    if (code_obj == nullptr || frame_addr->instr_ptr == nullptr) {
        return ErrorKind::FrameError;
    }

    // In Python 3.13+, instr_ptr points to the current instruction (not past it),
    // so _PyInterpreterFrame_LASTI = instr_ptr - _PyCode_CODE(code) with no -1.
    _Py_CODEUNIT* code_units = reinterpret_cast<_Py_CODEUNIT*>(code_obj);
    const int lasti =
      static_cast<int>((frame_addr->instr_ptr - code_units) -
                       static_cast<ptrdiff_t>(offsetof(PyCodeObject, co_code_adaptive) / sizeof(_Py_CODEUNIT)));
    auto maybe_frame = Frame::get(echion, code_obj, lasti);
    if (!maybe_frame) {
        return ErrorKind::FrameError;
    }

    auto& frame = maybe_frame->get();
#else
    if (frame_addr->f_code == nullptr || frame_addr->prev_instr == nullptr) {
        return ErrorKind::FrameError;
    }

    const int lasti =
      static_cast<int>((frame_addr->prev_instr - reinterpret_cast<_Py_CODEUNIT*>(frame_addr->f_code)) -
                       static_cast<ptrdiff_t>(offsetof(PyCodeObject, co_code_adaptive) / sizeof(_Py_CODEUNIT)));
    auto maybe_frame = Frame::get(echion, frame_addr->f_code, lasti);
    if (!maybe_frame) {
        return ErrorKind::FrameError;
    }

    auto& frame = maybe_frame->get();
#endif // PY_VERSION_HEX >= 0x030d0000
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
    *prev_addr = (&frame == &INVALID_FRAME) ? NULL : reinterpret_cast<PyObject*>(py_frame.f_back);
#endif // PY_VERSION_HEX >= 0x030b0000

    return std::ref(frame);
}

// ----------------------------------------------------------------------------
Result<std::reference_wrapper<Frame>>
Frame::get(EchionSampler& echion, PyCodeObject* code_addr, int lasti)
{
    // Read co_firstlineno before the cache lookup so it is part of the key.
    // This prevents ABA-problem false hits: if Python frees a PyCodeObject and allocates
    // a new one at the same address, co_firstlineno will differ and we get a cache miss
    // (triggering a fresh read) instead of returning a stale frame (e.g. "<module>").
    // We read only the single int field to keep the cost of cache hits low.
    int firstlineno;
    {
        auto* firstlineno_addr =
          reinterpret_cast<decltype(PyCodeObject::co_firstlineno)*>( // NOLINT(performance-no-int-to-ptr)
            reinterpret_cast<uintptr_t>(code_addr) + offsetof(PyCodeObject, co_firstlineno));
        if (copy_type(firstlineno_addr, firstlineno)) {
            return std::ref(INVALID_FRAME);
        }
    }

    auto frame_key = Frame::key(code_addr, lasti, firstlineno);

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
    new_frame->code_object = reinterpret_cast<uintptr_t>(code_addr);
    new_frame->lasti = lasti;
    new_frame->first_lineno = firstlineno;
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

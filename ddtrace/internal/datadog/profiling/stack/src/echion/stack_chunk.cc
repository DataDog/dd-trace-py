#include <echion/stack_chunk.h>

#if PY_VERSION_HEX >= 0x030b0000

// ----------------------------------------------------------------------------
Result<void>
StackChunk::update(_PyStackChunk* chunk_addr)
{
    return update_with_depth(chunk_addr, 0);
}

// ----------------------------------------------------------------------------
Result<void>
StackChunk::update_with_depth(_PyStackChunk* chunk_addr, size_t depth)
{
    if (depth >= kMaxChunkDepth) {
        return ErrorKind::StackChunkError;
    }

    _PyStackChunk chunk;

    if (copy_type(chunk_addr, chunk)) {
        return ErrorKind::StackChunkError;
    }

    // It's possible that the memory we read is corrupted/not valid anymore and the
    // chunk.size is not meaningful. Weed out those cases here to make sure we don't
    // try to allocate absurd amounts of memory.
    if (chunk.size > MAX_CHUNK_SIZE) {
        return ErrorKind::StackChunkError;
    }

    origin = chunk_addr;
    // if data_capacity is not enough, reallocate.
    if (chunk.size > data_capacity) {
        data_capacity = std::max(chunk.size, data_capacity);
        data.resize(data_capacity);
    }

    // Copy the data up until the size of the chunk
    if (copy_generic(chunk_addr, data.data(), chunk.size)) {
        return ErrorKind::StackChunkError;
    }

    // Store the size we actually copied. This is critical for bounds checking in StackChunk::resolve.
    // We must NOT read chunk->size from the copied data because a race condition can cause
    // it to be larger than what was actually copied, leading to out-of-bounds access.
    copied_size = chunk.size;

    if (chunk.previous != NULL) {
        if (chunk.previous == chunk_addr) {
            previous = nullptr;
            return Result<void>::ok();
        }
        if (previous == nullptr)
            previous = std::make_unique<StackChunk>();

        auto update_success = previous->update_with_depth(reinterpret_cast<_PyStackChunk*>(chunk.previous), depth + 1);
        if (!update_success) {
            previous = nullptr;
        }
    }

    return Result<void>::ok();
}

// ----------------------------------------------------------------------------
void*
StackChunk::resolve(void* address)
{
    // If data is not properly initialized, simply return the address
    if (!is_valid()) {
        return address;
    }

    // Use copied_size for bounds checking, NOT chunk->size from the copied data.
    // A race condition during copying can cause the header's size field to be larger
    // than what was actually copied, leading to out-of-bounds access and SEGV.
    if (address >= origin && address < reinterpret_cast<char*>(origin) + copied_size) {
        return data.data() + (reinterpret_cast<char*>(address) - reinterpret_cast<char*>(origin));
    }

    if (previous)
        return previous->resolve(address);

    return address;
}

// ----------------------------------------------------------------------------
bool
StackChunk::is_valid() const
{
    return data_capacity > 0 && copied_size > 0 && copied_size >= sizeof(_PyStackChunk) && data.size() >= copied_size &&
           data.data() != nullptr && origin != nullptr;
}

#endif // PY_VERSION_HEX >= 0x030b0000
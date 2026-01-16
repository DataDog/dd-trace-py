#include <echion/stack_chunk.h>

#if PY_VERSION_HEX >= 0x030b0000

// ----------------------------------------------------------------------------
Result<void>
StackChunk::update(_PyStackChunk* chunk_addr)
{
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

    if (chunk.previous != NULL) {
        if (previous == nullptr)
            previous = std::make_unique<StackChunk>();

        auto update_success = previous->update(reinterpret_cast<_PyStackChunk*>(chunk.previous));
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

    _PyStackChunk* chunk = reinterpret_cast<_PyStackChunk*>(data.data());

    // Check if this chunk contains the address
    if (address >= origin && address < reinterpret_cast<char*>(origin) + chunk->size)
        return reinterpret_cast<char*>(chunk) + (reinterpret_cast<char*>(address) - reinterpret_cast<char*>(origin));

    if (previous)
        return previous->resolve(address);

    return address;
}

// ----------------------------------------------------------------------------
bool
StackChunk::is_valid() const
{
    return data_capacity > 0 && data.size() > 0 && data.size() >= sizeof(_PyStackChunk) && data.data() != nullptr &&
           origin != nullptr;
}

#endif // PY_VERSION_HEX >= 0x030b0000
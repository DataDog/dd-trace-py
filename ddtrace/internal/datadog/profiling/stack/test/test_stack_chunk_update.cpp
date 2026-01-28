#include <gtest/gtest.h>

#include <unistd.h>

#include <echion/stack_chunk.h>
#include <echion/vm.h>

#if PY_VERSION_HEX >= 0x030b0000
TEST(StackChunkUpdate, HandlesSelfReferentialChunk)
{
    _set_pid(getpid());

    _PyStackChunk chunk{};
    chunk.size = sizeof(chunk);
    chunk.previous = &chunk;

    StackChunk stack;
    auto result = stack.update(&chunk);

    EXPECT_TRUE(result);
}
#endif
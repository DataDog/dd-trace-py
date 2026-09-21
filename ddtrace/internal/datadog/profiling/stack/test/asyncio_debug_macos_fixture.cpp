#include <echion/cpython/asyncio_debug.h>

// Build real Mach-O images so discovery parses loader-produced metadata, not a mock section pointer.
#if defined(ASYNCIO_FIXTURE_UNDERSIZED)
__attribute__((section("__DATA,AsyncioDebug"), used)) static uint64_t debug_table = 0;
#elif defined(ASYNCIO_FIXTURE_INVALID)
__attribute__((section("__DATA,AsyncioDebug"), used)) static PyAsyncioDebugOffsets debug_table{};
#elif defined(ASYNCIO_FIXTURE_VALID)
__attribute__((section("__DATA,AsyncioDebug"),
               used)) static PyAsyncioDebugOffsets debug_table = { { 512, 8, 16, 24, 25, 32, 40 },
                                                                   { 4096, 128 },
                                                                   { 1024, 8, 16, 256 } };
#endif

// dladdr on this symbol identifies the loaded image even in the fixture without an AsyncioDebug section.
extern "C" void
asyncio_fixture_anchor()
{}

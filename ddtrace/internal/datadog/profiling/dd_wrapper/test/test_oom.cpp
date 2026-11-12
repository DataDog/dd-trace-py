#include "ddup_interface.hpp"
#include "profiler_state.hpp"
#include "test_utils.hpp"
#include <gtest/gtest.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <sys/resource.h>

#if defined(__has_feature)
#if __has_feature(address_sanitizer) || __has_feature(thread_sanitizer) || __has_feature(memory_sanitizer)
#define DD_TEST_UNDER_SANITIZER 1
#endif
#endif
#if defined(__SANITIZE_ADDRESS__) || defined(__SANITIZE_THREAD__)
#define DD_TEST_UNDER_SANITIZER 1
#endif

static size_t
vm_size_bytes()
{
    FILE* f = std::fopen("/proc/self/status", "r");
    if (f == nullptr) {
        return 0;
    }
    char line[256];
    size_t kib = 0;
    while (std::fgets(line, sizeof(line), f) != nullptr) {
        if (std::sscanf(line, "VmSize: %zu kB", &kib) == 1) {
            break;
        }
    }
    std::fclose(f);
    return kib * 1024;
}

// libdatadog reports out-of-memory with a static message, so the status has
// `flags == 0` and a non-null `err`. Interning must report that as a failure
// instead of handing back the never-written out-parameter as a string id.
void
intern_string_under_address_space_cap()
{
    configure("my_test_service", "my_test_env", "0.0.1", "https://127.0.0.1:9126", "cpython", "3.10.6", "3.100", 256);
    auto maybe_dict = Datadog::ProfilerState::get().get_profiles_dictionary();
    if (!maybe_dict) {
        std::_Exit(3);
    }

    const size_t vsz = vm_size_bytes();
    if (vsz == 0) {
        std::_Exit(4);
    }
    const rlim_t cap = vsz + 1024 * 1024;
    const struct rlimit limit = { cap, cap };
    if (setrlimit(RLIMIT_AS, &limit) != 0) {
        std::_Exit(5);
    }

    // No heap use in the loop: only the dictionary should run out of memory.
    char buf[128];
    for (int i = 0; i < 50'000'000; i++) {
        const int n = std::snprintf(buf, sizeof(buf), "odoo.addons.demo.models.res_partner._compute_field_%d", i);
        auto id = Datadog::intern_string(std::string_view(buf, static_cast<size_t>(n)));
        if (!id) {
            std::_Exit(0);
        }
        ddog_CharSlice out;
        auto res = ddog_prof_ProfilesDictionary_get_str(&out, *maybe_dict, *id);
        if (res.err != nullptr || out.len != static_cast<size_t>(n) || std::memcmp(out.ptr, buf, out.len) != 0) {
            std::_Exit(2);
        }
    }
    // The cap was never hit, so the test proved nothing.
    std::_Exit(1);
}

TEST(OomDeathTest, InternStringReportsOutOfMemory)
{
#if defined(DD_VALGRIND)
    // The address-space cap is too tight for valgrind.
    GTEST_SKIP();
#elif defined(DD_TEST_UNDER_SANITIZER) || !defined(__linux__)
    GTEST_SKIP() << "needs Linux /proc and an RLIMIT_AS cap, which sanitizers cannot run under";
#else
    EXPECT_EXIT(intern_string_under_address_space_cap(), ::testing::ExitedWithCode(0), "");
#endif
}

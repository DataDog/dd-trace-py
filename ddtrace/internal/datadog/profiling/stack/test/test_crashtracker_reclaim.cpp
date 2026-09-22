#include "echion/danger.h"

#include <gtest/gtest.h>

TEST(CrashtrackerReclaim, NativeSoBasename)
{
    EXPECT_TRUE(fname_is_ddtrace_native_so("_native.so"));
    EXPECT_TRUE(fname_is_ddtrace_native_so("_native.abi3.so"));
    EXPECT_TRUE(fname_is_ddtrace_native_so("_native.cpython-312-x86_64-linux-gnu.so"));
    EXPECT_TRUE(fname_is_ddtrace_native_so("_native.cpython-312-darwin.so"));
    EXPECT_TRUE(fname_is_ddtrace_native_so("_native"));
    EXPECT_FALSE(fname_is_ddtrace_native_so("native.so"));
    EXPECT_FALSE(fname_is_ddtrace_native_so("_native_other.so"));
    EXPECT_FALSE(fname_is_ddtrace_native_so("libforeign.so"));
    EXPECT_FALSE(fname_is_ddtrace_native_so("ddtrace.so"));
    EXPECT_FALSE(fname_is_ddtrace_native_so(nullptr));
}

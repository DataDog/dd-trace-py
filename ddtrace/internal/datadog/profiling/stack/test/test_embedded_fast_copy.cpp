#include <echion/vm.h>
#include <gtest/gtest.h>

// Test that fast memory copy is disabled when Python runs as an embedded
// interpreter.  This binary is not named "python*", so is_python_embedded()
// returns true and init_safe_copy() never installs the SIGSEGV/SIGBUS
// handlers.  vm.cc is compiled into this binary, so the constructor has
// already fired on the state that the assertions below read.
TEST(EmbeddedFastCopy, FastCopyDisabledWhenEmbedded)
{
    EXPECT_TRUE(fast_copy_user_disabled)
      << "fast_copy_user_disabled must be true when the process exe is not a python binary";
    EXPECT_FALSE(fast_copy_active) << "fast_copy_active must be false in an embedded interpreter";
}

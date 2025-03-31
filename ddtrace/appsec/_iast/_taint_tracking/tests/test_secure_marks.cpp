#include <tests/test_common.hpp>
#include <taint_tracking/taint_range.h>

using SecureMarksTest = ::testing::Test;

TEST_F(SecureMarksTest, AddAndCheckSingleMark)
{
    TaintRange taint_range;
    taint_range.add_secure_mark(VulnerabilityType::SQL_INJECTION);
    EXPECT_TRUE(taint_range.has_secure_mark(VulnerabilityType::SQL_INJECTION));
    EXPECT_FALSE(taint_range.has_secure_mark(VulnerabilityType::XSS));
}

TEST_F(SecureMarksTest, AddMultipleMarks)
{
    TaintRange taint_range;
    taint_range.add_secure_mark(VulnerabilityType::SQL_INJECTION);
    taint_range.add_secure_mark(VulnerabilityType::XSS);
    EXPECT_TRUE(taint_range.has_secure_mark(VulnerabilityType::SQL_INJECTION));
    EXPECT_TRUE(taint_range.has_secure_mark(VulnerabilityType::XSS));
    EXPECT_FALSE(taint_range.has_secure_mark(VulnerabilityType::PATH_TRAVERSAL));
}

TEST_F(SecureMarksTest, ResetClearsMarks)
{
    TaintRange taint_range;
    taint_range.add_secure_mark(VulnerabilityType::SQL_INJECTION);
    taint_range.add_secure_mark(VulnerabilityType::XSS);
    EXPECT_TRUE(taint_range.has_secure_mark(VulnerabilityType::SQL_INJECTION));
    EXPECT_TRUE(taint_range.has_secure_mark(VulnerabilityType::XSS));

    taint_range.reset();
    EXPECT_FALSE(taint_range.has_secure_mark(VulnerabilityType::SQL_INJECTION));
    EXPECT_FALSE(taint_range.has_secure_mark(VulnerabilityType::XSS));
}

TEST_F(SecureMarksTest, AllVulnerabilityTypes)
{
    TaintRange taint_range;

    // Test all vulnerability types
    taint_range.add_secure_mark(VulnerabilityType::CODE_INJECTION);
    taint_range.add_secure_mark(VulnerabilityType::COMMAND_INJECTION);
    taint_range.add_secure_mark(VulnerabilityType::HEADER_INJECTION);
    taint_range.add_secure_mark(VulnerabilityType::INSECURE_COOKIE);
    taint_range.add_secure_mark(VulnerabilityType::NO_HTTPONLY_COOKIE);
    taint_range.add_secure_mark(VulnerabilityType::NO_SAMESITE_COOKIE);
    taint_range.add_secure_mark(VulnerabilityType::PATH_TRAVERSAL);
    taint_range.add_secure_mark(VulnerabilityType::SQL_INJECTION);
    taint_range.add_secure_mark(VulnerabilityType::SSRF);
    taint_range.add_secure_mark(VulnerabilityType::STACKTRACE_LEAK);
    taint_range.add_secure_mark(VulnerabilityType::WEAK_CIPHER);
    taint_range.add_secure_mark(VulnerabilityType::WEAK_HASH);
    taint_range.add_secure_mark(VulnerabilityType::WEAK_RANDOMNESS);
    taint_range.add_secure_mark(VulnerabilityType::XSS);

    // Verify all marks are set
    EXPECT_TRUE(taint_range.has_secure_mark(VulnerabilityType::CODE_INJECTION));
    EXPECT_TRUE(taint_range.has_secure_mark(VulnerabilityType::COMMAND_INJECTION));
    EXPECT_TRUE(taint_range.has_secure_mark(VulnerabilityType::HEADER_INJECTION));
    EXPECT_TRUE(taint_range.has_secure_mark(VulnerabilityType::INSECURE_COOKIE));
    EXPECT_TRUE(taint_range.has_secure_mark(VulnerabilityType::NO_HTTPONLY_COOKIE));
    EXPECT_TRUE(taint_range.has_secure_mark(VulnerabilityType::NO_SAMESITE_COOKIE));
    EXPECT_TRUE(taint_range.has_secure_mark(VulnerabilityType::PATH_TRAVERSAL));
    EXPECT_TRUE(taint_range.has_secure_mark(VulnerabilityType::SQL_INJECTION));
    EXPECT_TRUE(taint_range.has_secure_mark(VulnerabilityType::SSRF));
    EXPECT_TRUE(taint_range.has_secure_mark(VulnerabilityType::STACKTRACE_LEAK));
    EXPECT_TRUE(taint_range.has_secure_mark(VulnerabilityType::WEAK_CIPHER));
    EXPECT_TRUE(taint_range.has_secure_mark(VulnerabilityType::WEAK_HASH));
    EXPECT_TRUE(taint_range.has_secure_mark(VulnerabilityType::WEAK_RANDOMNESS));
    EXPECT_TRUE(taint_range.has_secure_mark(VulnerabilityType::XSS));
}

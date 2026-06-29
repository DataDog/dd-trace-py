#include "cpu_sample_ring.hpp"

#include <gtest/gtest.h>

#include <cstdint>

using Datadog::CpuTimer::CpuSampleRing;
using Datadog::CpuTimer::RawSample;

namespace {

RawSample
make_sample(uint64_t id)
{
    RawSample sample{};
    sample.cpu_delta_ns = 1'000 + id;
    sample.python_thread_id = 2'000 + id;
    sample.native_tid = 3'000 + id;
    sample.depth = 2;
    sample.frames[0].code_object = static_cast<uintptr_t>(4'000 + id);
    sample.frames[0].lasti = static_cast<int>(5'000 + id);
    sample.frames[0].first_lineno = static_cast<int>(6'000 + id);
    sample.frames[1].code_object = static_cast<uintptr_t>(7'000 + id);
    sample.frames[1].lasti = static_cast<int>(8'000 + id);
    sample.frames[1].first_lineno = static_cast<int>(9'000 + id);
    return sample;
}

void
expect_sample_eq(const RawSample& actual, const RawSample& expected)
{
    EXPECT_EQ(actual.cpu_delta_ns, expected.cpu_delta_ns);
    EXPECT_EQ(actual.python_thread_id, expected.python_thread_id);
    EXPECT_EQ(actual.native_tid, expected.native_tid);
    EXPECT_EQ(actual.depth, expected.depth);
    for (uint16_t i = 0; i < expected.depth; i++) {
        EXPECT_EQ(actual.frames[i].code_object, expected.frames[i].code_object);
        EXPECT_EQ(actual.frames[i].lasti, expected.frames[i].lasti);
        EXPECT_EQ(actual.frames[i].first_lineno, expected.frames[i].first_lineno);
    }
}

} // namespace

TEST(CpuSampleRing, StartsEmpty)
{
    CpuSampleRing ring(4);
    RawSample out{};

    EXPECT_EQ(ring.capacity(), 4u);
    EXPECT_TRUE(ring.empty());
    EXPECT_FALSE(ring.pop_for_consumer(out));
}

TEST(CpuSampleRing, ProducerReserveDoesNotPublish)
{
    CpuSampleRing ring(4);
    RawSample out{};
    RawSample sample = make_sample(1);

    RawSample* reserved = ring.reserve_for_producer();
    ASSERT_NE(reserved, nullptr);
    *reserved = sample;

    EXPECT_TRUE(ring.empty());
    EXPECT_FALSE(ring.pop_for_consumer(out));

    ring.publish_for_producer();

    EXPECT_FALSE(ring.empty());
    ASSERT_TRUE(ring.pop_for_consumer(out));
    expect_sample_eq(out, sample);
    EXPECT_TRUE(ring.empty());
}

TEST(CpuSampleRing, CapacityKeepsOneSlotOpenToDistinguishFullFromEmpty)
{
    CpuSampleRing ring(4);
    RawSample samples[] = { make_sample(1), make_sample(2), make_sample(3) };

    for (const auto& sample : samples) {
        RawSample* reserved = ring.reserve_for_producer();
        ASSERT_NE(reserved, nullptr);
        *reserved = sample;
        ring.publish_for_producer();
    }

    EXPECT_EQ(ring.reserve_for_producer(), nullptr);

    for (const auto& expected : samples) {
        RawSample out{};
        ASSERT_TRUE(ring.pop_for_consumer(out));
        expect_sample_eq(out, expected);
    }

    RawSample out{};
    EXPECT_TRUE(ring.empty());
    EXPECT_FALSE(ring.pop_for_consumer(out));
}

TEST(CpuSampleRing, WraparoundPreservesFifoOrder)
{
    CpuSampleRing ring(3);

    RawSample first = make_sample(1);
    RawSample second = make_sample(2);
    RawSample third = make_sample(3);

    RawSample* reserved = ring.reserve_for_producer();
    ASSERT_NE(reserved, nullptr);
    *reserved = first;
    ring.publish_for_producer();

    reserved = ring.reserve_for_producer();
    ASSERT_NE(reserved, nullptr);
    *reserved = second;
    ring.publish_for_producer();

    RawSample out{};
    ASSERT_TRUE(ring.pop_for_consumer(out));
    expect_sample_eq(out, first);

    reserved = ring.reserve_for_producer();
    ASSERT_NE(reserved, nullptr);
    *reserved = third;
    ring.publish_for_producer();

    ASSERT_TRUE(ring.pop_for_consumer(out));
    expect_sample_eq(out, second);
    ASSERT_TRUE(ring.pop_for_consumer(out));
    expect_sample_eq(out, third);
    EXPECT_TRUE(ring.empty());
}

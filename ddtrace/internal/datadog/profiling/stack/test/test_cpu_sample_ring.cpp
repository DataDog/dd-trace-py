#include "cpu_sample_ring.hpp"

#include <gtest/gtest.h>

#include <cstdint>
#include <thread>

using Datadog::CpuTimer::CpuSampleRing;
using Datadog::CpuTimer::RawSample;

namespace {

int first_code_object;
int second_code_object;

RawSample
make_sample(uint64_t id)
{
    RawSample sample{};
    sample.cpu_delta_ns = 1'000 + id;
    sample.python_thread_id = 2'000 + id;
    sample.native_tid = 3'000 + id;
    sample.asyncio_task = 4'000 + id;
    sample.greenlet_id = 4'250 + id;
    sample.coroutine_fingerprint_count = 1;
    sample.coroutine_fingerprints[0].coroutine = 4'500 + id;
    sample.coroutine_fingerprints[0].code_object = reinterpret_cast<uintptr_t>(&first_code_object);
    sample.coroutine_fingerprints[0].lasti = static_cast<int>(4'600 + id);
    sample.coroutine_fingerprints[0].first_lineno = static_cast<int>(4'700 + id);
    sample.depth = 2;
    sample.frames[0].code_object = &first_code_object;
    sample.frames[0].lasti = static_cast<int>(5'000 + id);
    sample.frames[0].first_lineno = static_cast<int>(6'000 + id);
    sample.frames[1].code_object = &second_code_object;
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
    EXPECT_EQ(actual.asyncio_task, expected.asyncio_task);
    EXPECT_EQ(actual.greenlet_id, expected.greenlet_id);
    EXPECT_EQ(actual.coroutine_fingerprint_count, expected.coroutine_fingerprint_count);
    for (uint8_t i = 0; i < expected.coroutine_fingerprint_count; i++) {
        EXPECT_EQ(actual.coroutine_fingerprints[i].coroutine, expected.coroutine_fingerprints[i].coroutine);
        EXPECT_EQ(actual.coroutine_fingerprints[i].code_object, expected.coroutine_fingerprints[i].code_object);
        EXPECT_EQ(actual.coroutine_fingerprints[i].lasti, expected.coroutine_fingerprints[i].lasti);
        EXPECT_EQ(actual.coroutine_fingerprints[i].first_lineno, expected.coroutine_fingerprints[i].first_lineno);
    }
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
    CpuSampleRing ring;
    RawSample out{};

    EXPECT_EQ(ring.capacity(), 64u);
    EXPECT_FALSE(ring.pop_for_consumer(out));
}

TEST(CpuSampleRing, ProducerReserveDoesNotPublish)
{
    CpuSampleRing ring;
    RawSample out{};
    RawSample sample = make_sample(1);

    RawSample* reserved = ring.reserve_for_producer();
    ASSERT_NE(reserved, nullptr);
    *reserved = sample;

    EXPECT_FALSE(ring.pop_for_consumer(out));

    ring.publish_for_producer();

    ASSERT_TRUE(ring.pop_for_consumer(out));
    expect_sample_eq(out, sample);
    EXPECT_FALSE(ring.pop_for_consumer(out));
}

TEST(CpuSampleRing, CapacityKeepsOneSlotOpenToDistinguishFullFromEmpty)
{
    CpuSampleRing ring;

    for (uint64_t id = 1; id < ring.capacity(); id++) {
        RawSample* reserved = ring.reserve_for_producer();
        ASSERT_NE(reserved, nullptr);
        *reserved = make_sample(id);
        ring.publish_for_producer();
    }

    EXPECT_EQ(ring.reserve_for_producer(), nullptr);

    for (uint64_t expected_id = 1; expected_id < ring.capacity(); expected_id++) {
        RawSample out{};
        ASSERT_TRUE(ring.pop_for_consumer(out));
        expect_sample_eq(out, make_sample(expected_id));
    }

    RawSample out{};
    EXPECT_FALSE(ring.pop_for_consumer(out));
}

TEST(CpuSampleRing, WraparoundPreservesFifoOrder)
{
    CpuSampleRing ring;

    for (uint64_t id = 1; id < ring.capacity(); id++) {
        RawSample* reserved = ring.reserve_for_producer();
        ASSERT_NE(reserved, nullptr);
        *reserved = make_sample(id);
        ring.publish_for_producer();
    }

    constexpr uint64_t consumed_before_wrap = 32;
    RawSample out{};
    for (uint64_t expected_id = 1; expected_id <= consumed_before_wrap; expected_id++) {
        ASSERT_TRUE(ring.pop_for_consumer(out));
        expect_sample_eq(out, make_sample(expected_id));
    }

    const uint64_t first_wrapped_id = ring.capacity();
    const uint64_t last_wrapped_id = first_wrapped_id + consumed_before_wrap - 1;
    for (uint64_t id = first_wrapped_id; id <= last_wrapped_id; id++) {
        RawSample* reserved = ring.reserve_for_producer();
        ASSERT_NE(reserved, nullptr);
        *reserved = make_sample(id);
        ring.publish_for_producer();
    }

    EXPECT_EQ(ring.reserve_for_producer(), nullptr);
    for (uint64_t expected_id = consumed_before_wrap + 1; expected_id <= last_wrapped_id; expected_id++) {
        ASSERT_TRUE(ring.pop_for_consumer(out));
        expect_sample_eq(out, make_sample(expected_id));
    }
    EXPECT_FALSE(ring.pop_for_consumer(out));
}

TEST(CpuSampleRing, ConcurrentProducerAndConsumerPreserveWholeFifoSamples)
{
    constexpr uint64_t sample_count = 20'000;
    CpuSampleRing ring;

    std::thread producer([&] {
        for (uint64_t id = 1; id <= sample_count; id++) {
            RawSample* reserved;
            while ((reserved = ring.reserve_for_producer()) == nullptr) {
                std::this_thread::yield();
            }
            *reserved = make_sample(id);
            ring.publish_for_producer();
        }
    });

    for (uint64_t expected_id = 1; expected_id <= sample_count; expected_id++) {
        RawSample actual{};
        while (!ring.pop_for_consumer(actual)) {
            std::this_thread::yield();
        }
        expect_sample_eq(actual, make_sample(expected_id));
    }

    producer.join();
    RawSample out{};
    EXPECT_FALSE(ring.pop_for_consumer(out));
}

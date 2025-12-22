#include <gmock/gmock.h>
#include <gtest/gtest.h>

#include <functional>

#include <echion/stacks.h>

// ============================================================================
// Test Cases for FrameStack
//
// Note: the test cases only check the "contents" of the Frame by only setting
// and looking at the name field, which is a StringTable::Key (so, an integer).
// We make sure each Frame actually gets a different "ID" (name) so that we can
// have the right assertions later to check the FrameStack is behaving as
// expected.
// ============================================================================

const constexpr size_t initial_capacity = 2048;

TEST(FrameStack, Construction)
{
    FrameStack stack;
    EXPECT_EQ(stack.size(), 0);
    EXPECT_TRUE(stack.empty());
}

TEST(FrameStack, PushBack)
{
    FrameStack stack;
    Frame frame1(1);
    Frame frame2(2);
    Frame frame3(3);

    stack.push_back(std::ref(frame1));
    EXPECT_EQ(stack.size(), 1);
    EXPECT_FALSE(stack.empty());
    EXPECT_EQ(stack[0].get().name, 1);

    stack.push_back(std::ref(frame2));
    EXPECT_EQ(stack.size(), 2);
    EXPECT_EQ(stack[1].get().name, 2);

    stack.push_back(std::ref(frame3));
    EXPECT_EQ(stack.size(), 3);
    EXPECT_EQ(stack[2].get().name, 3);
}

TEST(FrameStack, PushFront)
{
    FrameStack stack;
    Frame frame1(1);
    Frame frame2(2);
    Frame frame3(3);

    stack.push_front(std::ref(frame1));
    EXPECT_EQ(stack.size(), 1);
    EXPECT_EQ(stack[0].get().name, 1);

    stack.push_front(std::ref(frame2));
    EXPECT_EQ(stack.size(), 2);
    EXPECT_EQ(stack[0].get().name, 2);
    EXPECT_EQ(stack[1].get().name, 1);

    stack.push_front(std::ref(frame3));
    EXPECT_EQ(stack.size(), 3);
    EXPECT_EQ(stack[0].get().name, 3);
    EXPECT_EQ(stack[1].get().name, 2);
    EXPECT_EQ(stack[2].get().name, 1);
}

TEST(FrameStack, MixedPushFrontPushBack)
{
    FrameStack stack;
    Frame frame1(1);
    Frame frame2(2);
    Frame frame3(3);
    Frame frame4(4);

    stack.push_back(std::ref(frame1));  // [1]
    stack.push_front(std::ref(frame2)); // [2, 1]
    stack.push_back(std::ref(frame3));  // [2, 1, 3]
    stack.push_front(std::ref(frame4)); // [4, 2, 1, 3]

    EXPECT_EQ(stack.size(), 4);
    EXPECT_EQ(stack[0].get().name, 4);
    EXPECT_EQ(stack[1].get().name, 2);
    EXPECT_EQ(stack[2].get().name, 1);
    EXPECT_EQ(stack[3].get().name, 3);
}

TEST(FrameStack, Clear)
{
    FrameStack stack;
    Frame frame1(1);
    Frame frame2(2);

    stack.push_back(std::ref(frame1));
    stack.push_back(std::ref(frame2));
    EXPECT_EQ(stack.size(), 2);

    stack.clear();
    EXPECT_EQ(stack.size(), 0);
    EXPECT_TRUE(stack.empty());

    // Ensure stack is usable after clear
    Frame frame3(3);
    stack.push_back(std::ref(frame3));
    EXPECT_EQ(stack.size(), 1);
    EXPECT_EQ(stack[0].get().name, 3);
}

TEST(FrameStack, ForwardIterator)
{
    FrameStack stack;
    Frame frame1(1);
    Frame frame2(2);
    Frame frame3(3);

    stack.push_back(std::ref(frame1));
    stack.push_back(std::ref(frame2));
    stack.push_back(std::ref(frame3));

    std::vector<StringTable::Key> values;
    for (auto it = stack.begin(); it != stack.end(); ++it) {
        values.push_back((*it).get().name);
    }

    EXPECT_THAT(values, ::testing::ElementsAre(1, 2, 3));
}

TEST(FrameStack, ReverseIterator)
{
    FrameStack stack;
    Frame frame1(1);
    Frame frame2(2);
    Frame frame3(3);

    stack.push_back(std::ref(frame1));
    stack.push_back(std::ref(frame2));
    stack.push_back(std::ref(frame3));

    std::vector<StringTable::Key> values;
    for (auto it = stack.rbegin(); it != stack.rend(); ++it) {
        values.push_back((*it).get().name);
    }

    EXPECT_THAT(values, ::testing::ElementsAre(3, 2, 1));
}

TEST(FrameStack, GrowthOnPushBack)
{
    // Push enough elements to force growth beyond initial_capacity (2048)
    FrameStack stack;
    std::vector<Frame> frames;

    const int num_frames = initial_capacity + 100;
    // Create enough frames to trigger growth
    for (int i = 0; i < num_frames; ++i) {
        frames.emplace_back(i);
    }

    // Push back enough elements to force growth
    for (int i = 0; i < num_frames; ++i) {
        stack.push_back(std::ref(frames[i]));
    }

    EXPECT_EQ(stack.size(), num_frames);

    // Verify all values are correct after growth
    for (int i = 0; i < num_frames; ++i) {
        EXPECT_EQ(stack[i].get().name, i);
    }
}

TEST(FrameStack, GrowthOnPushFront)
{
    // Push enough elements to force growth beyond initial_capacity (2048)
    FrameStack stack;
    std::vector<Frame> frames;

    const int num_frames = initial_capacity + 100;
    for (int i = 0; i < num_frames; ++i) {
        frames.emplace_back(i);
    }

    // Push front enough elements to force growth
    for (int i = 0; i < num_frames; ++i) {
        stack.push_front(std::ref(frames[i]));
    }

    EXPECT_EQ(stack.size(), num_frames);

    // Verify all values are correct (reversed order)
    for (int i = 0; i < num_frames; ++i) {
        EXPECT_EQ(stack[i].get().name, num_frames - 1 - i);
    }
}

TEST(FrameStack, GrowthWithMixedOperations)
{
    // Test growth with alternating push_front and push_back
    FrameStack stack;
    std::vector<Frame> frames;

    const int num_frames = initial_capacity + 200;
    for (int i = 0; i < num_frames; ++i) {
        frames.emplace_back(i);
    }

    // Alternate between push_front and push_back to force growth
    for (int i = 0; i < num_frames; ++i) {
        if (i % 2 == 0) {
            stack.push_back(std::ref(frames[i]));
        } else {
            stack.push_front(std::ref(frames[i]));
        }
    }

    EXPECT_EQ(stack.size(), num_frames);
}

TEST(FrameStack, GrowthPreservesOrder)
{
    FrameStack stack;
    std::vector<Frame> frames;

    for (int i = 0; i < 2048 * 2; ++i) {
        frames.emplace_back(i);
    }

    // Add elements using both push_back and push_front
    for (int i = 0; i < 1024; ++i) {
        stack.push_back(std::ref(frames[i]));
    }

    for (int i = 1024; i < 2048 * 2; ++i) {
        stack.push_front(std::ref(frames[i]));
    }

    EXPECT_EQ(stack.size(), 2048 * 2);

    // After growth, verify we can iterate correctly
    size_t count = 0;
    for (auto it = stack.begin(); it != stack.end(); ++it) {
        ++count;
    }
    EXPECT_EQ(count, 2048 * 2);
}

TEST(FrameStack, ClearAndReuse)
{
    FrameStack stack;
    std::vector<Frame> frames;

    for (int i = 0; i < 1024; ++i) {
        frames.emplace_back(i);
    }

    // Fill the stack
    for (int i = 0; i < 1024; ++i) {
        stack.push_back(std::ref(frames[i]));
    }
    EXPECT_EQ(stack.size(), 1024);

    // Clear and refill
    stack.clear();
    EXPECT_EQ(stack.size(), 0);

    for (int i = 0; i < 1024; ++i) {
        stack.push_back(std::ref(frames[i]));
    }
    EXPECT_EQ(stack.size(), 1024);

    for (int i = 0; i < 1024; ++i) {
        EXPECT_EQ(stack[i].get().name, i);
    }
}

TEST(FrameStack, EmptyAfterClear)
{
    FrameStack stack;
    Frame frame1(1);

    stack.push_back(std::ref(frame1));
    EXPECT_FALSE(stack.empty());

    stack.clear();
    EXPECT_TRUE(stack.empty());
    EXPECT_EQ(stack.size(), 0);
}

TEST(FrameStack, IteratorConsistency)
{
    FrameStack stack;
    std::vector<Frame> frames;

    // Create all frames first to avoid vector reallocation invalidating references
    for (int i = 0; i < 10; ++i) {
        frames.emplace_back(i);
    }

    for (int i = 0; i < 10; ++i) {
        ASSERT_EQ(frames[i].name, i);
        stack.push_back(std::ref(frames[i]));
    }

    // Test that forward and reverse iterators are consistent
    std::vector<StringTable::Key> forward_values;
    std::vector<StringTable::Key> reverse_values;

    for (auto it = stack.begin(); it != stack.end(); ++it) {
        forward_values.push_back(it->name);
    }

    for (auto it = stack.rbegin(); it != stack.rend(); ++it) {
        reverse_values.push_back(it->name);
    }

    std::reverse(reverse_values.begin(), reverse_values.end());
    EXPECT_EQ(forward_values, reverse_values);
}

TEST(FrameStack, LargeStack)
{
    FrameStack stack;
    std::vector<Frame> frames;

    const int large_size = 1000;
    for (int i = 0; i < large_size; ++i) {
        frames.emplace_back(i);
    }

    // Fill with large number of elements
    for (int i = 0; i < large_size; ++i) {
        stack.push_back(std::ref(frames[i]));
    }

    EXPECT_EQ(stack.size(), large_size);

    // Verify all elements
    for (int i = 0; i < large_size; ++i) {
        EXPECT_EQ(stack[i].get().name, i);
    }
}

TEST(FrameStack, AlternatingGrowth)
{
    FrameStack stack;
    std::vector<Frame> frames;

    const int num_frames = initial_capacity + 300;
    for (int i = 0; i < num_frames; ++i) {
        frames.emplace_back(i);
    }

    // Alternate direction to test balanced growth
    for (int i = 0; i < num_frames; ++i) {
        if (i % 4 == 0 || i % 4 == 1) {
            stack.push_back(std::ref(frames[i]));
        } else {
            stack.push_front(std::ref(frames[i]));
        }
    }

    EXPECT_EQ(stack.size(), num_frames);

    // Ensure we can still iterate
    size_t count = 0;
    for (auto it = stack.begin(); it != stack.end(); ++it) {
        ++count;
    }
    EXPECT_EQ(count, num_frames);
}

TEST(FrameStack, PopBack)
{
    FrameStack stack;
    Frame frame1(1);
    Frame frame2(2);
    Frame frame3(3);

    stack.push_back(std::ref(frame1));
    stack.push_back(std::ref(frame2));
    stack.push_back(std::ref(frame3));
    EXPECT_EQ(stack.size(), 3);

    stack.pop_back();
    EXPECT_EQ(stack.size(), 2);
    EXPECT_EQ(stack[0].get().name, 1);
    EXPECT_EQ(stack[1].get().name, 2);

    stack.pop_back();
    EXPECT_EQ(stack.size(), 1);
    EXPECT_EQ(stack[0].get().name, 1);

    stack.pop_back();
    EXPECT_EQ(stack.size(), 0);
    EXPECT_TRUE(stack.empty());
}

TEST(FrameStack, PopFront)
{
    FrameStack stack;
    Frame frame1(1);
    Frame frame2(2);
    Frame frame3(3);

    stack.push_back(std::ref(frame1));
    stack.push_back(std::ref(frame2));
    stack.push_back(std::ref(frame3));
    EXPECT_EQ(stack.size(), 3);

    stack.pop_front();
    EXPECT_EQ(stack.size(), 2);
    EXPECT_EQ(stack[0].get().name, 2);
    EXPECT_EQ(stack[1].get().name, 3);

    stack.pop_front();
    EXPECT_EQ(stack.size(), 1);
    EXPECT_EQ(stack[0].get().name, 3);

    stack.pop_front();
    EXPECT_EQ(stack.size(), 0);
    EXPECT_TRUE(stack.empty());
}

TEST(FrameStack, PopBackEmpty)
{
    FrameStack stack;
    EXPECT_TRUE(stack.empty());

    // pop_back on empty stack should not crash
    stack.pop_back();
    EXPECT_TRUE(stack.empty());
}

TEST(FrameStack, PopFrontEmpty)
{
    FrameStack stack;
    EXPECT_TRUE(stack.empty());

    // pop_front on empty stack should not crash
    stack.pop_front();
    EXPECT_TRUE(stack.empty());
}

TEST(FrameStack, MixedPushPop)
{
    FrameStack stack;
    Frame frame1(1);
    Frame frame2(2);
    Frame frame3(3);
    Frame frame4(4);

    stack.push_back(std::ref(frame1));  // [1]
    stack.push_back(std::ref(frame2));  // [1, 2]
    stack.pop_front();                  // [2]
    stack.push_front(std::ref(frame3)); // [3, 2]
    stack.push_back(std::ref(frame4));  // [3, 2, 4]
    stack.pop_back();                   // [3, 2]

    EXPECT_EQ(stack.size(), 2);
    EXPECT_EQ(stack[0].get().name, 3);
    EXPECT_EQ(stack[1].get().name, 2);
}

int
main(int argc, char** argv)
{
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}

#include "echion/cache.h"

#include <gtest/gtest.h>

#include <memory>

TEST(LRUCacheCapacity, ClampsCapacityToOne)
{
    LRUCache<int, int> cache(0);

    EXPECT_EQ(cache.capacity(), 1);
    cache.set_capacity(0);
    EXPECT_EQ(cache.capacity(), 1);
}

TEST(LRUCacheCapacity, ShrinkingEvictsLeastRecentlyUsedEntries)
{
    LRUCache<int, int> cache(3);
    cache.store(1, std::make_unique<int>(1));
    cache.store(2, std::make_unique<int>(2));
    cache.store(3, std::make_unique<int>(3));

    ASSERT_TRUE(cache.lookup(1).has_value());
    cache.set_capacity(2);

    EXPECT_EQ(cache.capacity(), 2);
    EXPECT_TRUE(cache.lookup(1).has_value());
    EXPECT_FALSE(cache.lookup(2).has_value());
    EXPECT_TRUE(cache.lookup(3).has_value());

    cache.set_capacity(1);
    EXPECT_TRUE(cache.lookup(3).has_value());
    EXPECT_FALSE(cache.lookup(1).has_value());

    cache.store(4, std::make_unique<int>(4));
    EXPECT_FALSE(cache.lookup(3).has_value());
    EXPECT_TRUE(cache.lookup(4).has_value());
}

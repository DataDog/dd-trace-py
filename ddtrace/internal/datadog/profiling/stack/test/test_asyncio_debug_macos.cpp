#include <echion/cpython/asyncio_debug.h>

#include <gtest/gtest.h>

#include <array>
#include <dlfcn.h>
#include <mach-o/dyld.h>
#include <vector>

namespace {

struct LoadedImage
{
    const mach_header* header;
    const char* path;
};

// Restrict enumeration to fixture images so an installed or built-in _asyncio cannot hide a discovery failure.
std::vector<LoadedImage> images;
size_t visited_images = 0;

} // namespace

// CMake redirects only the test target's dyld enumeration calls. Section lookup and memory reads stay real.
extern "C" uint32_t
test_asyncio_dyld_image_count()
{
    return static_cast<uint32_t>(images.size());
}

extern "C" const mach_header*
test_asyncio_dyld_get_image_header(uint32_t index)
{
    ++visited_images;
    return images.at(index).header;
}

extern "C" const char*
test_asyncio_dyld_get_image_name(uint32_t index)
{
    return images.at(index).path;
}

class AsyncioMachODiscovery : public ::testing::Test
{
  protected:
    enum Fixture : size_t
    {
        Missing,
        Undersized,
        Invalid,
        Valid,
    };
    std::array<void*, 4> handles{};
    std::array<LoadedImage, 4> fixtures{};

    void SetUp() override
    {
        images.clear();
        visited_images = 0;
        constexpr std::array paths{ ASYNCIO_MISSING_FIXTURE_PATH,
                                    ASYNCIO_UNDERSIZED_FIXTURE_PATH,
                                    ASYNCIO_INVALID_FIXTURE_PATH,
                                    ASYNCIO_VALID_FIXTURE_PATH };
        for (size_t i = 0; i < paths.size(); ++i) {
            handles[i] = dlopen(paths[i], RTLD_NOW | RTLD_LOCAL);
            ASSERT_NE(handles[i], nullptr) << dlerror();
            void* anchor = dlsym(handles[i], "asyncio_fixture_anchor");
            ASSERT_NE(anchor, nullptr) << dlerror();
            Dl_info info{};
            ASSERT_NE(dladdr(anchor, &info), 0);
            fixtures[i] = { static_cast<const mach_header*>(info.dli_fbase), paths[i] };
        }
    }

    void TearDown() override
    {
        images.clear();
        for (auto* handle : handles) {
            if (handle != nullptr) {
                EXPECT_EQ(dlclose(handle), 0);
            }
        }
    }

    void expect_valid_offsets()
    {
        auto offsets = find_asyncio_debug_offsets();
        ASSERT_TRUE(offsets);
        EXPECT_EQ(offsets->interpreter_tasks_head, 128);
        EXPECT_EQ(offsets->thread_tasks_head, 256);
    }
};

TEST_F(AsyncioMachODiscovery, RejectsMissingSection)
{
    images = { fixtures[Missing] };
    EXPECT_FALSE(find_asyncio_debug_offsets());
    EXPECT_EQ(visited_images, 1);
}

TEST_F(AsyncioMachODiscovery, RejectsUndersizedSection)
{
    images = { fixtures[Undersized] };
    EXPECT_FALSE(find_asyncio_debug_offsets());
    EXPECT_EQ(visited_images, 1);
}

TEST_F(AsyncioMachODiscovery, RejectsInvalidTable)
{
    images = { fixtures[Invalid] };
    EXPECT_FALSE(find_asyncio_debug_offsets());
    EXPECT_EQ(visited_images, 1);
}

TEST_F(AsyncioMachODiscovery, SkipsInvalidImagesAndFindsValidTable)
{
    images = { fixtures[Missing], fixtures[Undersized], fixtures[Invalid], fixtures[Valid] };
    expect_valid_offsets();
    EXPECT_EQ(visited_images, 4);
}

TEST_F(AsyncioMachODiscovery, RetriesAfterAnEarlierMiss)
{
    images = { fixtures[Missing] };
    EXPECT_FALSE(find_asyncio_debug_offsets());
    images.push_back(fixtures[Valid]);
    expect_valid_offsets();
    EXPECT_EQ(visited_images, 3);
}

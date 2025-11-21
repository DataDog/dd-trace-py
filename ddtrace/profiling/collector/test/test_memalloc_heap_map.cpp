#include "../_memalloc_heap_map.hpp"
#include "../_memalloc_tb.h"
#include "../_pymacro.h"
#include <Python.h>
#include <cstdlib>
#include <gtest/gtest.h>
#include <set>
#include <vector>

// Global variables to pass data to Python callback
static traceback_t* g_test_traceback = nullptr;
static void* g_test_ptr = nullptr;
static size_t g_test_size = 0;

// C function that Python can call to create a traceback
// This is called from Python context, so we have an active frame
extern "C"
{
    static PyObject* test_create_traceback_callback(PyObject* self, PyObject* args)
    {
        // We're now executing in Python context, so we should have a frame
        g_test_traceback = traceback_t::get_traceback(10, g_test_ptr, g_test_size, PYMEM_DOMAIN_OBJ, g_test_size);
        Py_RETURN_NONE;
    }

    static PyMethodDef TestMethods[] = {
        { "create_traceback", test_create_traceback_callback, METH_NOARGS, "Create a traceback for testing" },
        { nullptr, nullptr, 0, nullptr }
    };

    static PyModuleDef TestModule = { PyModuleDef_HEAD_INIT, "test_helper", nullptr, -1, TestMethods };
}

class MemallocHeapMapTest : public ::testing::Test
{
  protected:
    void SetUp() override
    {
        // Initialize Python if not already initialized
        if (!Py_IsInitialized()) {
            Py_Initialize();
        }
        // Initialize traceback module
        ASSERT_TRUE(traceback_t::init());

        // Register our test helper module
        PyObject* module = PyModule_Create(&TestModule);
        if (module) {
            PyObject* main_module = PyImport_AddModule("__main__");
            PyObject* main_dict = PyModule_GetDict(main_module);
            PyDict_SetItemString(main_dict, "test_helper", module);
            Py_DECREF(module);
        }
    }

    void TearDown() override
    {
        // Clean up traceback module
        traceback_t::deinit();
        g_test_traceback = nullptr;
    }

    // Helper to create a mock traceback_t for testing
    // Note: This creates a minimal traceback_t that won't crash on destruction
    // Returns nullptr if no Python frame is available (tests should handle this)
    traceback_t* create_mock_traceback(void* ptr, size_t size)
    {
        // The challenge: get_traceback() needs an active Python frame, but when
        // we're executing C++ code, there's no active frame. We need to execute
        // Python code and call get_traceback from within that execution context.
        //
        // For unit tests, we'll use PyRun_SimpleString to execute code that
        // will create a frame. However, this still might not work because
        // once PyRun_SimpleString returns, we're back in C++ with no frame.
        //
        // The real solution would be to create a Python C extension function
        // that calls get_traceback, but for now we'll try to get a frame.

        g_test_ptr = ptr;
        g_test_size = size;
        g_test_traceback = nullptr;

        // Execute Python code that calls our callback function
        // This ensures we're in a Python execution context when get_traceback is called
        PyRun_SimpleString("test_helper.create_traceback()\n");

        traceback_t* ret = g_test_traceback;
        g_test_traceback = nullptr;
        return ret;
    }
};

// Test default constructor
TEST_F(MemallocHeapMapTest, DefaultConstructor)
{
    memalloc_heap_map map;
    EXPECT_EQ(map.size(), 0);
}

// Test size() on empty map
TEST_F(MemallocHeapMapTest, EmptyMapSize)
{
    memalloc_heap_map map;
    EXPECT_EQ(map.size(), 0);
    EXPECT_FALSE(map.contains(nullptr));
}

// Test insert() and size()
TEST_F(MemallocHeapMapTest, InsertAndSize)
{
    memalloc_heap_map map;

    void* ptr1 = malloc(100);
    void* ptr2 = malloc(200);

    traceback_t* tb1 = create_mock_traceback(ptr1, 100);
    traceback_t* tb2 = create_mock_traceback(ptr2, 200);

    ASSERT_NE(tb1, nullptr);
    ASSERT_NE(tb2, nullptr);

    // Insert first entry
    traceback_t* prev = map.insert(ptr1, tb1);
    EXPECT_EQ(prev, nullptr); // Should be nullptr for new insertion
    EXPECT_EQ(map.size(), 1);
    EXPECT_TRUE(map.contains(ptr1));

    // Insert second entry
    prev = map.insert(ptr2, tb2);
    EXPECT_EQ(prev, nullptr);
    EXPECT_EQ(map.size(), 2);
    EXPECT_TRUE(map.contains(ptr2));

    // Clean up
    free(ptr1);
    free(ptr2);
    // Note: traceback_t objects will be deleted by map destructor
}

// Test insert() replacing existing value
TEST_F(MemallocHeapMapTest, InsertReplace)
{
    memalloc_heap_map map;

    void* ptr = malloc(100);

    traceback_t* tb1 = create_mock_traceback(ptr, 100);
    traceback_t* tb2 = create_mock_traceback(ptr, 200);

    ASSERT_NE(tb1, nullptr);
    ASSERT_NE(tb2, nullptr);

    // Insert first traceback
    traceback_t* prev = map.insert(ptr, tb1);
    EXPECT_EQ(prev, nullptr);
    EXPECT_EQ(map.size(), 1);

    // Replace with second traceback
    prev = map.insert(ptr, tb2);
    EXPECT_EQ(prev, tb1);     // Should return old value
    EXPECT_EQ(map.size(), 1); // Size should remain 1

    // Clean up old traceback that was replaced
    delete tb1;

    free(ptr);
}

// Test contains()
TEST_F(MemallocHeapMapTest, Contains)
{
    memalloc_heap_map map;

    void* ptr1 = malloc(100);
    void* ptr2 = malloc(200);
    void* ptr3 = malloc(300);

    traceback_t* tb1 = create_mock_traceback(ptr1, 100);
    traceback_t* tb2 = create_mock_traceback(ptr2, 200);

    ASSERT_NE(tb1, nullptr);
    ASSERT_NE(tb2, nullptr);

    map.insert(ptr1, tb1);
    map.insert(ptr2, tb2);

    EXPECT_TRUE(map.contains(ptr1));
    EXPECT_TRUE(map.contains(ptr2));
    EXPECT_FALSE(map.contains(ptr3));
    EXPECT_FALSE(map.contains(nullptr));

    free(ptr1);
    free(ptr2);
    free(ptr3);
}

// Test remove()
TEST_F(MemallocHeapMapTest, Remove)
{
    memalloc_heap_map map;

    void* ptr1 = malloc(100);
    void* ptr2 = malloc(200);

    traceback_t* tb1 = create_mock_traceback(ptr1, 100);
    traceback_t* tb2 = create_mock_traceback(ptr2, 200);

    ASSERT_NE(tb1, nullptr);
    ASSERT_NE(tb2, nullptr);

    map.insert(ptr1, tb1);
    map.insert(ptr2, tb2);

    EXPECT_EQ(map.size(), 2);

    // Remove existing entry
    traceback_t* removed = map.remove(ptr1);
    EXPECT_EQ(removed, tb1);
    EXPECT_EQ(map.size(), 1);
    EXPECT_FALSE(map.contains(ptr1));
    EXPECT_TRUE(map.contains(ptr2));

    // Remove non-existent entry
    removed = map.remove(ptr1);
    EXPECT_EQ(removed, nullptr);
    EXPECT_EQ(map.size(), 1);

    // Clean up removed traceback
    delete tb1;

    free(ptr1);
    free(ptr2);
}

// Test remove() on empty map
TEST_F(MemallocHeapMapTest, RemoveFromEmpty)
{
    memalloc_heap_map map;

    void* ptr = malloc(100);

    traceback_t* removed = map.remove(ptr);
    EXPECT_EQ(removed, nullptr);
    EXPECT_EQ(map.size(), 0);

    free(ptr);
}

// Test iterator begin() and end()
TEST_F(MemallocHeapMapTest, IteratorBeginEnd)
{
    memalloc_heap_map map;

    // Empty map: begin() should equal end()
    auto it_begin = map.begin();
    auto it_end = map.end();
    EXPECT_EQ(it_begin, it_end);

    // Add some entries
    void* ptr1 = malloc(100);
    void* ptr2 = malloc(200);

    traceback_t* tb1 = create_mock_traceback(ptr1, 100);
    traceback_t* tb2 = create_mock_traceback(ptr2, 200);

    ASSERT_NE(tb1, nullptr);
    ASSERT_NE(tb2, nullptr);

    map.insert(ptr1, tb1);
    map.insert(ptr2, tb2);

    // Now begin() should not equal end()
    it_begin = map.begin();
    it_end = map.end();
    EXPECT_NE(it_begin, it_end);

    free(ptr1);
    free(ptr2);
}

// Test iterator iteration
TEST_F(MemallocHeapMapTest, IteratorIteration)
{
    memalloc_heap_map map;

    const int num_entries = 10;
    std::vector<void*> ptrs;
    std::vector<traceback_t*> tbs;

    // Create entries
    for (int i = 0; i < num_entries; i++) {
        void* ptr = malloc((i + 1) * 100);
        traceback_t* tb = create_mock_traceback(ptr, (i + 1) * 100);
        if (tb != nullptr) {
            ptrs.push_back(ptr);
            tbs.push_back(tb);
            map.insert(ptr, tb);
        }
    }

    EXPECT_EQ(map.size(), num_entries);

    // Iterate and collect keys
    std::set<void*> found_keys;
    int count = 0;
    for (auto it = map.begin(); it != map.end(); ++it) {
        auto pair = *it;
        found_keys.insert(pair.first);
        EXPECT_NE(pair.second, nullptr);
        count++;
    }

    EXPECT_EQ(count, num_entries);
    EXPECT_EQ(found_keys.size(), num_entries);

    // Verify all keys were found
    for (void* ptr : ptrs) {
        EXPECT_TRUE(found_keys.find(ptr) != found_keys.end());
        free(ptr);
    }
}

// Test iterator post-increment
TEST_F(MemallocHeapMapTest, IteratorPostIncrement)
{
    memalloc_heap_map map;

    void* ptr1 = malloc(100);
    void* ptr2 = malloc(200);

    traceback_t* tb1 = create_mock_traceback(ptr1, 100);
    traceback_t* tb2 = create_mock_traceback(ptr2, 200);

    ASSERT_NE(tb1, nullptr);
    ASSERT_NE(tb2, nullptr);

    map.insert(ptr1, tb1);
    map.insert(ptr2, tb2);

    auto it = map.begin();
    auto it_copy = it++;

    // it_copy should point to first element, it should point to second
    EXPECT_NE(it_copy, it);
    EXPECT_NE(it, map.end());

    free(ptr1);
    free(ptr2);
}

// Test destructive_copy_from()
TEST_F(MemallocHeapMapTest, DestructiveCopyFrom)
{
    memalloc_heap_map src;
    memalloc_heap_map dst;

    void* ptr1 = malloc(100);
    void* ptr2 = malloc(200);
    void* ptr3 = malloc(300);

    traceback_t* tb1 = create_mock_traceback(ptr1, 100);
    traceback_t* tb2 = create_mock_traceback(ptr2, 200);
    traceback_t* tb3 = create_mock_traceback(ptr3, 300);

    ASSERT_NE(tb1, nullptr);
    ASSERT_NE(tb2, nullptr);
    ASSERT_NE(tb3, nullptr);

    // Add entries to source
    src.insert(ptr1, tb1);
    src.insert(ptr2, tb2);
    src.insert(ptr3, tb3);

    EXPECT_EQ(src.size(), 3);
    EXPECT_EQ(dst.size(), 0);

    // Copy from source to destination
    dst.destructive_copy_from(src);

    EXPECT_EQ(src.size(), 0); // Source should be cleared
    EXPECT_EQ(dst.size(), 3); // Destination should have all entries

    // Verify entries are in destination
    EXPECT_TRUE(dst.contains(ptr1));
    EXPECT_TRUE(dst.contains(ptr2));
    EXPECT_TRUE(dst.contains(ptr3));

    // Verify entries are not in source
    EXPECT_FALSE(src.contains(ptr1));
    EXPECT_FALSE(src.contains(ptr2));
    EXPECT_FALSE(src.contains(ptr3));

    free(ptr1);
    free(ptr2);
    free(ptr3);
}

// Test destructive_copy_from() with empty source
TEST_F(MemallocHeapMapTest, DestructiveCopyFromEmpty)
{
    memalloc_heap_map src;
    memalloc_heap_map dst;

    void* ptr = malloc(100);
    traceback_t* tb = create_mock_traceback(ptr, 100);

    ASSERT_NE(tb, nullptr);

    dst.insert(ptr, tb);
    EXPECT_EQ(dst.size(), 1);

    // Copy from empty source
    dst.destructive_copy_from(src);

    EXPECT_EQ(src.size(), 0);
    EXPECT_EQ(dst.size(), 1); // Destination should be unchanged

    free(ptr);
}

// Test that destructor cleans up traceback_t objects
TEST_F(MemallocHeapMapTest, DestructorCleansUp)
{
    {
        memalloc_heap_map map;

        void* ptr1 = malloc(100);
        void* ptr2 = malloc(200);

        traceback_t* tb1 = create_mock_traceback(ptr1, 100);
        traceback_t* tb2 = create_mock_traceback(ptr2, 200);

        ASSERT_NE(tb1, nullptr);
        ASSERT_NE(tb2, nullptr);

        map.insert(ptr1, tb1);
        map.insert(ptr2, tb2);

        // Map goes out of scope here, destructor should clean up tb1 and tb2
    }

    // If we get here without crashing, destructor worked correctly
    EXPECT_TRUE(true);

    // Note: We can't easily verify the traceback_t objects were deleted
    // without adding instrumentation, but if they weren't deleted we'd likely
    // see memory leaks or crashes
}

int
main(int argc, char** argv)
{
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}

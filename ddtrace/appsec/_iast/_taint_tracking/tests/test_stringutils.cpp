#include <Python.h>
#include <gtest/gtest.h>
#include <pybind11/embed.h>
#include <pybind11/pybind11.h>

#include <Utils/StringUtils.h>

namespace py = pybind11;

class PyEnvTest : public ::testing::Test
{
  protected:
    void SetUp() override
    {
        // Initialize the Python interpreter for pybind11
        py::initialize_interpreter();
    }

    void TearDown() override
    {
        // Finalize the Python interpreter
        py::finalize_interpreter();
    }
};

// get_unique_id ===
TEST_F(PyEnvTest, TestGetUniqueId)
{
    PyObject* py_str = PyUnicode_FromString("test_string");
    auto expected_value = reinterpret_cast<uintptr_t>(py_str);
    EXPECT_EQ(get_unique_id(py_str), expected_value);

    PyObject* nullobject = nullptr;
    expected_value = reinterpret_cast<uintptr_t>(nullobject);
    EXPECT_EQ(get_unique_id(nullobject), expected_value);

    Py_DECREF(py_str);
}

// PyReMatch_Check ===

// Test case to check a valid `re.Match` object
TEST_F(PyEnvTest, TestPyReMatchValidMatchObject)
{
    py::object re_module = py::module_::import("re");
    py::object match_obj = re_module.attr("match")("a", "a");

    ASSERT_TRUE(PyReMatch_Check(match_obj.ptr()));
}

// Test case to check an invalid object (not `re.Match`)
TEST_F(PyEnvTest, TEstPyReMatchInvalidNonMatchObject)
{
    py::object non_match_obj = py::int_(42); // Not a `re.Match` object

    ASSERT_FALSE(PyReMatch_Check(non_match_obj.ptr()));
}

// Test case to check a `None` (null) object
TEST_F(PyEnvTest, TEstPyReMatchNullObject)
{
    PyObject* null_obj = Py_None;

    ASSERT_FALSE(PyReMatch_Check(null_obj));
}

// set_fast_tainted_if_notinterned_unicode ===
TEST_F(PyEnvTest, FastTaintedNullptrReturnsTrue)
{
    EXPECT_TRUE(is_notinterned_notfasttainted_unicode(nullptr));
}

TEST_F(PyEnvTest, FastTaintedNonUnicodeReturnsFalse)
{
    PyObject* non_unicode = PyLong_FromLong(42);
    EXPECT_FALSE(is_notinterned_notfasttainted_unicode(non_unicode));
    Py_DECREF(non_unicode);
}

TEST_F(PyEnvTest, FastTaintedInternedUnicodeReturnsTrue)
{
    PyObject* interned_unicode = PyUnicode_InternFromString("interned");
    EXPECT_TRUE(is_notinterned_notfasttainted_unicode(interned_unicode));
    Py_DECREF(interned_unicode);
}

TEST_F(PyEnvTest, NonInternedUnicodeWithHashMinusOneReturnsTrue)
{
    PyObject* non_interned_unicode = PyUnicode_FromString("non_interned");
    reinterpret_cast<PyASCIIObject*>(non_interned_unicode)->hash = -1;
    EXPECT_TRUE(is_notinterned_notfasttainted_unicode(non_interned_unicode));
    Py_DECREF(non_interned_unicode);
}

TEST_F(PyEnvTest, NonInternedUnicodeWithHiddenNotMatchingHashReturnsTrue)
{
    PyObject* non_interned_unicode = PyUnicode_FromString("non_interned");
    reinterpret_cast<PyASCIIObject*>(non_interned_unicode)->hash = 12345;
    reinterpret_cast<_PyASCIIObject_State_Hidden*>(&reinterpret_cast<PyASCIIObject*>(non_interned_unicode)->state)
      ->hidden = 54321;
    EXPECT_TRUE(is_notinterned_notfasttainted_unicode(non_interned_unicode));
    Py_DECREF(non_interned_unicode);
}

TEST_F(PyEnvTest, NonInternedUnicodeWithHiddenMatchingHashReturnsFalse)
{
    PyObject* non_interned_unicode = PyUnicode_FromString("non_interned");
    reinterpret_cast<PyASCIIObject*>(non_interned_unicode)->hash = 12345;
    reinterpret_cast<_PyASCIIObject_State_Hidden*>(&reinterpret_cast<PyASCIIObject*>(non_interned_unicode)->state)
      ->hidden = GET_HASH_KEY(12345);
    EXPECT_FALSE(is_notinterned_notfasttainted_unicode(non_interned_unicode));
    Py_DECREF(non_interned_unicode);
}

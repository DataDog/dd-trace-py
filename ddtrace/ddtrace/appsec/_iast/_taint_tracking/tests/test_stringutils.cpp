#include <tests/test_common.hpp>
#include <utils/string_utils.h>

using GetUniqueId = PyEnvCheck;

TEST_F(GetUniqueId, TestGetUniqueId)
{
    PyObject* py_str = PyUnicode_FromString("test_string");
    auto expected_value = reinterpret_cast<uintptr_t>(py_str);
    EXPECT_EQ(get_unique_id(py_str), expected_value);

    PyObject* nullobject = nullptr;
    expected_value = reinterpret_cast<uintptr_t>(nullobject);
    EXPECT_EQ(get_unique_id(nullobject), expected_value);

    Py_DECREF(py_str);
}

using PyReMatchCheck = PyEnvWithContext;

TEST_F(PyReMatchCheck, TestPyReMatchValidMatchObject)
{
    py::object re_module = py::module_::import("re");
    py::object match_obj = re_module.attr("match")("a", "a");

    ASSERT_TRUE(PyReMatch_Check(match_obj.ptr()));
}

TEST_F(PyReMatchCheck, TestPyReMatchInvalidNonMatchObject)
{
    py::object non_match_obj = py::int_(42); // Not a `re.Match` object

    ASSERT_FALSE(PyReMatch_Check(non_match_obj.ptr()));
}

TEST_F(PyReMatchCheck, TEstPyReMatchNullObject)
{
    PyObject* null_obj = Py_None;

    ASSERT_FALSE(PyReMatch_Check(null_obj));
}

using PyIOBaseCheck = PyEnvWithContext;

TEST_F(PyIOBaseCheck, TestPyIOBaseValidObject)
{
    py::object io_module = py::module_::import("io");
    py::object stringio_obj = io_module.attr("StringIO")("a");

    ASSERT_TRUE(PyIOBase_Check(stringio_obj.ptr()));
}

TEST_F(PyIOBaseCheck, TestPyIOBaseInvalidObject)
{
    py::object non_io_obj = py::int_(42); // Not a `_io._IOBase` object

    ASSERT_FALSE(PyIOBase_Check(non_io_obj.ptr()));
}

TEST_F(PyIOBaseCheck, TestPyIOBaseNullObject)
{
    PyObject* null_obj = Py_None;

    ASSERT_FALSE(PyIOBase_Check(null_obj));
}

using IsFastTaintedCheck = PyEnvCheck;

TEST_F(IsFastTaintedCheck, FastTaintedNullptrReturnsTrue)
{
    EXPECT_TRUE(is_notinterned_notfasttainted_unicode(nullptr));
}

TEST_F(IsFastTaintedCheck, FastTaintedNonUnicodeReturnsFalse)
{
    PyObject* non_unicode = PyLong_FromLong(42);
    EXPECT_FALSE(is_notinterned_notfasttainted_unicode(non_unicode));
    Py_DECREF(non_unicode);
}

TEST_F(IsFastTaintedCheck, FastTaintedInternedUnicodeReturnsTrue)
{
    PyObject* interned_unicode = PyUnicode_InternFromString("interned");
    EXPECT_TRUE(is_notinterned_notfasttainted_unicode(interned_unicode));
    Py_DECREF(interned_unicode);
}

TEST_F(IsFastTaintedCheck, NonInternedUnicodeWithHashMinusOneReturnsTrue)
{
    PyObject* non_interned_unicode = PyUnicode_FromString("non_interned");
    reinterpret_cast<PyASCIIObject*>(non_interned_unicode)->hash = -1;
    EXPECT_TRUE(is_notinterned_notfasttainted_unicode(non_interned_unicode));
    Py_DECREF(non_interned_unicode);
}

TEST_F(IsFastTaintedCheck, NonInternedUnicodeWithHiddenNotMatchingHashReturnsTrue)
{
    PyObject* non_interned_unicode = PyUnicode_FromString("non_interned");
    reinterpret_cast<PyASCIIObject*>(non_interned_unicode)->hash = 12345;
    reinterpret_cast<_PyASCIIObject_State_Hidden*>(&reinterpret_cast<PyASCIIObject*>(non_interned_unicode)->state)
      ->hidden = 54321;
    EXPECT_TRUE(is_notinterned_notfasttainted_unicode(non_interned_unicode));
    Py_DECREF(non_interned_unicode);
}

TEST_F(IsFastTaintedCheck, NonInternedUnicodeWithHiddenMatchingHashReturnsFalse)
{
    PyObject* non_interned_unicode = PyUnicode_FromString("non_interned");
    reinterpret_cast<PyASCIIObject*>(non_interned_unicode)->hash = 12345;
    reinterpret_cast<_PyASCIIObject_State_Hidden*>(&reinterpret_cast<PyASCIIObject*>(non_interned_unicode)->state)
      ->hidden = GET_HASH_KEY(12345);
    EXPECT_FALSE(is_notinterned_notfasttainted_unicode(non_interned_unicode));
    Py_DECREF(non_interned_unicode);
}

using SetFastTaintedCheck = PyEnvCheck;

TEST_F(SetFastTaintedCheck, NullptrDoesNothing)
{
    set_fast_tainted_if_notinterned_unicode(nullptr);
    // No assertion needed, just ensure no crash
}

TEST_F(SetFastTaintedCheck, NonUnicodeDoesNothing)
{
    PyObject* non_unicode = PyLong_FromLong(42);
    set_fast_tainted_if_notinterned_unicode(non_unicode);
    // No assertion needed, just ensure no crash
    Py_DECREF(non_unicode);
}

TEST_F(SetFastTaintedCheck, InternedUnicodeDoesNothing)
{
    PyObject* interned_unicode = PyUnicode_InternFromString("interned");
    set_fast_tainted_if_notinterned_unicode(interned_unicode);
    // No assertion needed, just ensure no crash
    Py_DECREF(interned_unicode);
}

TEST_F(SetFastTaintedCheck, NonInternedUnicodeSetsHidden)
{
    PyObject* non_interned_unicode = PyUnicode_FromString("non_interned");
    reinterpret_cast<PyASCIIObject*>(non_interned_unicode)->hash = 12345;
    set_fast_tainted_if_notinterned_unicode(non_interned_unicode);
    const _PyASCIIObject_State_Hidden* e =
      (_PyASCIIObject_State_Hidden*)&(((PyASCIIObject*)non_interned_unicode)->state);
    EXPECT_EQ(e->hidden, GET_HASH_KEY(12345));
    Py_DECREF(non_interned_unicode);
}

TEST_F(SetFastTaintedCheck, NonInternedUnicodeWithHashMinusOneSetsHidden)
{
    PyObject* non_interned_unicode = PyUnicode_FromString("non_interned");
    reinterpret_cast<PyASCIIObject*>(non_interned_unicode)->hash = -1;
    set_fast_tainted_if_notinterned_unicode(non_interned_unicode);
    Py_hash_t hash = PyObject_Hash(non_interned_unicode);
    const _PyASCIIObject_State_Hidden* e =
      (_PyASCIIObject_State_Hidden*)&(((PyASCIIObject*)non_interned_unicode)->state);
    EXPECT_EQ(e->hidden, GET_HASH_KEY(hash));
    Py_DECREF(non_interned_unicode);
}

using IsTextCheck = PyEnvCheck;

TEST_F(IsTextCheck, NullptrReturnsFalse)
{
    EXPECT_FALSE(is_text(nullptr));
}

TEST_F(IsTextCheck, UnicodeReturnsTrue)
{
    PyObject* unicode_obj = PyUnicode_FromString("test");
    EXPECT_TRUE(is_text(unicode_obj));
    Py_DECREF(unicode_obj);
}

TEST_F(IsTextCheck, BytesReturnsTrue)
{
    PyObject* bytes_obj = PyBytes_FromString("test");
    EXPECT_TRUE(is_text(bytes_obj));
    Py_DECREF(bytes_obj);
}

TEST_F(IsTextCheck, ByteArrayReturnsTrue)
{
    PyObject* bytes_obj = PyBytes_FromString("test");
    PyObject* bytearray_obj = PyByteArray_FromObject(bytes_obj);
    EXPECT_TRUE(is_text(bytearray_obj));
    Py_DECREF(bytearray_obj);
    Py_DECREF(bytes_obj);
}

TEST_F(IsTextCheck, NonTextReturnsFalse)
{
    PyObject* non_text_obj = PyLong_FromLong(42);
    EXPECT_FALSE(is_text(non_text_obj));
    Py_DECREF(non_text_obj);
}

using IsTainteableCheck = PyEnvWithContext;

TEST_F(IsTainteableCheck, NullptrReturnsFalse)
{
    EXPECT_FALSE(is_tainteable(nullptr));
}

TEST_F(IsTainteableCheck, UnicodeReturnsTrue)
{
    PyObject* unicode_obj = PyUnicode_FromString("test");
    EXPECT_TRUE(is_tainteable(unicode_obj));
    Py_DECREF(unicode_obj);
}

TEST_F(IsTainteableCheck, BytesReturnsTrue)
{
    PyObject* bytes_obj = PyBytes_FromString("test");
    EXPECT_TRUE(is_tainteable(bytes_obj));
    Py_DECREF(bytes_obj);
}

TEST_F(IsTainteableCheck, ByteArrayReturnsTrue)
{
    PyObject* bytes_obj = PyBytes_FromString("test");
    PyObject* bytearray_obj = PyByteArray_FromObject(bytes_obj);
    EXPECT_TRUE(is_tainteable(bytearray_obj));
    Py_DECREF(bytearray_obj);
    Py_DECREF(bytes_obj);
}

TEST_F(IsTainteableCheck, NonTextReturnsFalse)
{
    PyObject* non_text_obj = PyLong_FromLong(42);
    EXPECT_FALSE(is_tainteable(non_text_obj));
    Py_DECREF(non_text_obj);
}

TEST_F(IsTainteableCheck, ReMatchReturnsTrue)
{
    py::object re = py::module_::import("re");
    py::object match = re.attr("match")("a", "a");
    EXPECT_TRUE(is_tainteable(match.ptr()));
}

using ArgsAreTextAndSameTypeCheck = PyEnvCheck;

TEST_F(ArgsAreTextAndSameTypeCheck, NullptrReturnsFalse)
{
    EXPECT_FALSE(args_are_text_and_same_type(nullptr, nullptr));
}

TEST_F(ArgsAreTextAndSameTypeCheck, MixedTypesReturnFalse)
{
    PyObject* unicode_obj = PyUnicode_FromString("test");
    PyObject* bytes_obj = PyBytes_FromString("test");
    EXPECT_FALSE(args_are_text_and_same_type(unicode_obj, bytes_obj));
    Py_DECREF(unicode_obj);
    Py_DECREF(bytes_obj);
}

TEST_F(ArgsAreTextAndSameTypeCheck, AllUnicodeReturnsTrue)
{
    PyObject* unicode_obj1 = PyUnicode_FromString("test1");
    PyObject* unicode_obj2 = PyUnicode_FromString("test2");
    EXPECT_TRUE(args_are_text_and_same_type(unicode_obj1, unicode_obj2));
    Py_DECREF(unicode_obj1);
    Py_DECREF(unicode_obj2);
}

TEST_F(ArgsAreTextAndSameTypeCheck, AllBytesReturnsTrue)
{
    PyObject* bytes_obj1 = PyBytes_FromString("test1");
    PyObject* bytes_obj2 = PyBytes_FromString("test2");
    EXPECT_TRUE(args_are_text_and_same_type(bytes_obj1, bytes_obj2));
    Py_DECREF(bytes_obj1);
    Py_DECREF(bytes_obj2);
}

TEST_F(ArgsAreTextAndSameTypeCheck, AllByteArrayReturnsTrue)
{
    PyObject* bytes_obj1 = PyBytes_FromString("test1");
    PyObject* bytearray_obj1 = PyByteArray_FromObject(bytes_obj1);
    PyObject* bytes_obj2 = PyBytes_FromString("test2");
    PyObject* bytearray_obj2 = PyByteArray_FromObject(bytes_obj2);
    EXPECT_TRUE(args_are_text_and_same_type(bytearray_obj1, bytearray_obj2));
    Py_DECREF(bytearray_obj1);
    Py_DECREF(bytearray_obj2);
    Py_DECREF(bytes_obj1);
    Py_DECREF(bytes_obj2);
}

TEST_F(ArgsAreTextAndSameTypeCheck, MixedTextTypesReturnFalse)
{
    PyObject* unicode_obj = PyUnicode_FromString("test");
    PyObject* bytes_obj = PyBytes_FromString("test");
    PyObject* bytearray_obj = PyByteArray_FromObject(bytes_obj);
    EXPECT_FALSE(args_are_text_and_same_type(unicode_obj, bytes_obj, bytearray_obj));
    Py_DECREF(unicode_obj);
    Py_DECREF(bytes_obj);
    Py_DECREF(bytearray_obj);
}

using PyObjectToStringCheck = PyEnvCheck;

TEST_F(PyObjectToStringCheck, NullptrReturnsEmptyString)
{
    EXPECT_STREQ(PyObjectToString(nullptr).c_str(), "");
}

TEST_F(PyObjectToStringCheck, UnicodeReturnsCorrectString)
{
    PyObject* unicode_obj = PyUnicode_FromString("test");
    EXPECT_STREQ(PyObjectToString(unicode_obj).c_str(), "test");
    Py_DECREF(unicode_obj);
}

TEST_F(PyObjectToStringCheck, NonUnicodeReturnsEmptyString)
{
    PyObject* non_unicode_obj = PyLong_FromLong(42);
    EXPECT_STREQ(PyObjectToString(non_unicode_obj).c_str(), "");
    Py_DECREF(non_unicode_obj);
}

using StringToPyObjectCheck = PyEnvCheck;

TEST_F(StringToPyObjectCheck, ConvertsToUnicode)
{
    std::string test_str = "test";
    py::object py_obj = StringToPyObject(test_str, PyTextType::UNICODE);
    EXPECT_TRUE(PyUnicode_Check(py_obj.ptr()));
    EXPECT_STREQ(PyUnicode_AsUTF8(py_obj.ptr()), test_str.c_str());
}

TEST_F(StringToPyObjectCheck, ConvertsToBytes)
{
    std::string test_str = "test";
    py::object py_obj = StringToPyObject(test_str, PyTextType::BYTES);
    EXPECT_TRUE(PyBytes_Check(py_obj.ptr()));
    EXPECT_STREQ(PyBytes_AsString(py_obj.ptr()), test_str.c_str());
}

TEST_F(StringToPyObjectCheck, ConvertsToByteArray)
{
    std::string test_str = "test";
    py::object py_obj = StringToPyObject(test_str, PyTextType::BYTEARRAY);
    EXPECT_TRUE(PyByteArray_Check(py_obj.ptr()));
    EXPECT_STREQ(PyByteArray_AsString(py_obj.ptr()), test_str.c_str());
}

TEST_F(StringToPyObjectCheck, InvalidTypeReturnsNone)
{
    std::string test_str = "test";
    py::object py_obj = StringToPyObject(test_str, PyTextType::OTHER);
    EXPECT_TRUE(py_obj.is_none());
}

using AnyTextObjectToStringCheck = PyEnvCheck;

TEST_F(AnyTextObjectToStringCheck, UnicodeReturnsCorrectString)
{
    auto unicode_obj = py::str("test");
    EXPECT_STREQ(AnyTextObjectToString(unicode_obj).c_str(), "test");
}

TEST_F(AnyTextObjectToStringCheck, BytesReturnsCorrectString)
{
    auto bytes_obj = py::bytes("test");
    EXPECT_STREQ(AnyTextObjectToString(bytes_obj).c_str(), "test");
}

TEST_F(AnyTextObjectToStringCheck, ByteArrayReturnsCorrectString)
{
    auto bytearray_obj = py::bytearray("test");
    EXPECT_STREQ(AnyTextObjectToString(bytearray_obj).c_str(), "test");
}

TEST_F(AnyTextObjectToStringCheck, NonTextReturnsEmptyString)
{
    auto non_text_obj = py::int_(42);
    EXPECT_STREQ(AnyTextObjectToString(non_text_obj).c_str(), "");
}

using PyObjectToPyTextCheck = PyEnvCheck;

TEST_F(PyObjectToPyTextCheck, UnicodeReturnsPyStr)
{
    PyObject* unicode_obj = PyUnicode_FromString("test");
    auto result = PyObjectToPyText(unicode_obj);
    ASSERT_TRUE(result.has_value());
    EXPECT_TRUE(py::isinstance<py::str>(result.value()));
    Py_DECREF(unicode_obj);
}

TEST_F(PyObjectToPyTextCheck, BytesReturnsPyBytes)
{
    PyObject* bytes_obj = PyBytes_FromString("test");
    auto result = PyObjectToPyText(bytes_obj);
    ASSERT_TRUE(result.has_value());
    EXPECT_TRUE(py::isinstance<py::bytes>(result.value()));
    Py_DECREF(bytes_obj);
}

TEST_F(PyObjectToPyTextCheck, ByteArrayReturnsPyByteArray)
{
    PyObject* bytes_obj = PyBytes_FromString("test");
    PyObject* bytearray_obj = PyByteArray_FromObject(bytes_obj);
    auto result = PyObjectToPyText(bytearray_obj);
    ASSERT_TRUE(result.has_value());
    EXPECT_TRUE(py::isinstance<py::bytearray>(result.value()));
    Py_DECREF(bytes_obj);
    Py_DECREF(bytearray_obj);
}

TEST_F(PyObjectToPyTextCheck, NonTextReturnsEmptyOptional)
{
    PyObject* non_text_obj = PyLong_FromLong(42);
    auto result = PyObjectToPyText(non_text_obj);
    EXPECT_FALSE(result.has_value());
    Py_DECREF(non_text_obj);
}

using GetPyTextTypeCheck = PyEnvCheck;

TEST_F(GetPyTextTypeCheck, UnicodeReturnsUnicodeType)
{
    PyObject* unicode_obj = PyUnicode_FromString("test");
    EXPECT_EQ(get_pytext_type(unicode_obj), PyTextType::UNICODE);
    Py_DECREF(unicode_obj);
}

TEST_F(GetPyTextTypeCheck, BytesReturnsBytesType)
{
    PyObject* bytes_obj = PyBytes_FromString("test");
    EXPECT_EQ(get_pytext_type(bytes_obj), PyTextType::BYTES);
    Py_DECREF(bytes_obj);
}

TEST_F(GetPyTextTypeCheck, ByteArrayReturnsByteArrayType)
{
    PyObject* bytes_obj = PyBytes_FromString("test");
    PyObject* bytearray_obj = PyByteArray_FromObject(bytes_obj);
    EXPECT_EQ(get_pytext_type(bytearray_obj), PyTextType::BYTEARRAY);
    Py_DECREF(bytearray_obj);
    Py_DECREF(bytes_obj);
}

TEST_F(GetPyTextTypeCheck, NonTextReturnsOtherType)
{
    PyObject* non_text_obj = PyLong_FromLong(42);
    EXPECT_EQ(get_pytext_type(non_text_obj), PyTextType::OTHER);
    Py_DECREF(non_text_obj);
}

using NewPyObjectIdCheck = PyEnvCheck;

TEST_F(NewPyObjectIdCheck, ValidTaintedUnicodeReturnsNewId)
{
    PyObject* tainted_obj = PyUnicode_FromString("tainted");
    PyObject* new_id_obj = new_pyobject_id(tainted_obj);

    ASSERT_NE(new_id_obj, nullptr);
    ASSERT_NE(tainted_obj, new_id_obj);
    EXPECT_TRUE(PyUnicode_Check(new_id_obj));
    EXPECT_STREQ(PyUnicode_AsUTF8(tainted_obj), PyUnicode_AsUTF8(new_id_obj));

    Py_DECREF(tainted_obj);
    Py_DECREF(new_id_obj);
}

TEST_F(NewPyObjectIdCheck, ValidTaintedBytesReturnsNewId)
{
    PyObject* tainted_obj = PyBytes_FromString("tainted");
    PyObject* new_id_obj = new_pyobject_id(tainted_obj);

    ASSERT_NE(new_id_obj, nullptr);
    ASSERT_NE(tainted_obj, new_id_obj);
    EXPECT_TRUE(PyBytes_Check(new_id_obj));
    EXPECT_STREQ(PyBytes_AsString(tainted_obj), PyBytes_AsString(new_id_obj));

    Py_DECREF(tainted_obj);
    Py_DECREF(new_id_obj);
}

TEST_F(NewPyObjectIdCheck, ValidTaintedByteArrayReturnsNewId)
{
    PyObject* bytes_obj = PyBytes_FromString("tainted");
    PyObject* bytearray_obj = PyByteArray_FromObject(bytes_obj);
    PyObject* new_id_obj = new_pyobject_id(bytearray_obj);

    ASSERT_NE(new_id_obj, nullptr);
    ASSERT_NE(bytes_obj, new_id_obj);
    ASSERT_NE(bytearray_obj, new_id_obj);
    EXPECT_TRUE(PyByteArray_Check(new_id_obj));
    EXPECT_STREQ(PyByteArray_AsString(bytearray_obj), PyByteArray_AsString(new_id_obj));

    Py_DECREF(bytearray_obj);
    Py_DECREF(bytes_obj);
}

TEST_F(NewPyObjectIdCheck, NullObjectReturnsNull)
{
    PyObject* new_id_obj = new_pyobject_id(nullptr);
    EXPECT_EQ(new_id_obj, nullptr);
}

TEST_F(NewPyObjectIdCheck, NonTextObjectReturnsSameObject)
{
    PyObject* non_text_obj = PyLong_FromLong(42);
    PyObject* new_id_obj = new_pyobject_id(non_text_obj);
    EXPECT_EQ(new_id_obj, non_text_obj);
    Py_DECREF(non_text_obj);
}

TEST_F(NewPyObjectIdCheck, WrongPointer)
{
    PyObject* wrong_object = reinterpret_cast<PyObject*>(0x12345);
    PyObject* new_id_obj = new_pyobject_id(wrong_object);
    EXPECT_EQ(new_id_obj, wrong_object);
}

TEST_F(NewPyObjectIdCheck, PyObjectNoType)
{
    PyObject* wrong_object = PyBytes_FromString("test");
    wrong_object->ob_type = nullptr;
    PyObject* new_id_obj = new_pyobject_id(wrong_object);
    EXPECT_EQ(new_id_obj, wrong_object);
}

using GetPyObjectSizeCheck = PyEnvCheck;

TEST_F(GetPyObjectSizeCheck, UnicodeReturnsCorrectSize)
{
    PyObject* unicode_obj = PyUnicode_FromString("test");
    EXPECT_EQ(get_pyobject_size(unicode_obj), 4);
    Py_DECREF(unicode_obj);
}

TEST_F(GetPyObjectSizeCheck, NonSingleCodepointUnicodeReturnsCorrectSize)
{
    PyObject* unicode_obj = PyUnicode_FromString("𝄞𝄞"); // Musical symbol G clef (non-single codepoint)
    EXPECT_EQ(get_pyobject_size(unicode_obj), 2);
    Py_DECREF(unicode_obj);
}

TEST_F(GetPyObjectSizeCheck, BytesReturnsCorrectSize)
{
    PyObject* bytes_obj = PyBytes_FromString("test");
    EXPECT_EQ(get_pyobject_size(bytes_obj), 4);
    Py_DECREF(bytes_obj);
}

TEST_F(GetPyObjectSizeCheck, NonSingleCodepointByteReturnsCorrectSize)
{
    PyObject* bytes_obj = PyBytes_FromString("𝄞𝄞");
    EXPECT_EQ(get_pyobject_size(bytes_obj), 8);
    Py_DECREF(bytes_obj);
}

TEST_F(GetPyObjectSizeCheck, ByteArrayReturnsCorrectSize)
{
    PyObject* bytes_obj = PyBytes_FromString("test");
    PyObject* bytearray_obj = PyByteArray_FromObject(bytes_obj);
    EXPECT_EQ(get_pyobject_size(bytearray_obj), 4);
    Py_DECREF(bytearray_obj);
    Py_DECREF(bytes_obj);
}

TEST_F(GetPyObjectSizeCheck, NonTextReturnsZero)
{
    PyObject* non_text_obj = PyLong_FromLong(42);
    EXPECT_EQ(get_pyobject_size(non_text_obj), 0);
    Py_DECREF(non_text_obj);
}

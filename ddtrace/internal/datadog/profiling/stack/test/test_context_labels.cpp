#include "echion/context_labels.h"

#include <gtest/gtest.h>

#include <sys/mman.h>
#include <unistd.h>

#include <cstring>
#include <initializer_list>
#include <memory>
#include <set>
#include <vector>

namespace {

// Zeroed, 8-byte aligned blocks that stand in for Python objects. ContextLookup only sees addresses and
// bytes, so the tests build every node and object by hand.
class FakeHeap
{
  public:
    uintptr_t alloc(size_t bytes)
    {
        blocks_.emplace_back(new uintptr_t[(bytes + 7) / 8]());
        return reinterpret_cast<uintptr_t>(blocks_.back().get());
    }

    template<typename T>
    static void put(uintptr_t address, size_t offset, T value)
    {
        std::memcpy(reinterpret_cast<void*>(address + offset), &value, sizeof(T));
    }

  private:
    std::vector<std::unique_ptr<uintptr_t[]>> blocks_;
};

// Copies with memcpy and fails on blocks marked unreadable.
struct TestCopy
{
    std::set<uintptr_t> unreadable;

    bool operator()(uintptr_t address, void* buffer, size_t size)
    {
        if (unreadable.count(address) != 0) {
            return false;
        }
        std::memcpy(buffer, reinterpret_cast<const void*>(address), size);
        return true;
    }
};

using Lookup = ContextLookup<TestCopy>;

constexpr uint32_t SPAN_ID = 16;
constexpr uint32_t PARENT_ID = 24;
constexpr uint32_t PARENT = 32;
constexpr uint32_t LOCAL_ROOT = 40;
constexpr uint32_t PARENT_CONTEXT = 48;
constexpr uint32_t SPAN_TYPE_ID = 56;
constexpr uint32_t FINISHED = 60;

constexpr uint32_t CTX_SPAN_ID = 16;
constexpr uint32_t CTX_LOCAL_ROOT = 24;
constexpr uint32_t CTX_SPAN_TYPE = 32;
constexpr uint32_t CTX_REACTIVATE = 36;

constexpr size_t OBJECT_SIZE = 96;

struct Pair
{
    uintptr_t key;
    uintptr_t value;
};

class ContextLabelsTest : public ::testing::Test
{
  protected:
    void SetUp() override
    {
        layout.context_type = type();
        layout.hamt_type = type();
        layout.bitmap_type = type();
        layout.array_type = type();
        layout.collision_type = type();
        layout.none = heap.alloc(sizeof(PyObject));
        layout.key = heap.alloc(sizeof(PyObject));
        layout.key_hash = 0x0b3a5c61; // a different 5-bit slot at each level
        layout.span_data_type = type();
        span_type_slot = reinterpret_cast<const void*>(type());
        layout.span_type = &span_type_slot;
        layout.tracer_context_type = type();
        layout.span = { 16, 48, SPAN_ID, PARENT_ID, PARENT, LOCAL_ROOT, PARENT_CONTEXT, SPAN_TYPE_ID, FINISHED, 0 };
        layout.context = { 16, 24, CTX_SPAN_ID, CTX_LOCAL_ROOT, CTX_SPAN_TYPE, CTX_REACTIVATE };
        layout.value_copy_size = 64;
    }

    uintptr_t type(uintptr_t base = 0)
    {
        const uintptr_t t = heap.alloc(sizeof(PyTypeObject));
        FakeHeap::put(t, offsetof(PyTypeObject, tp_base), base);
        return t;
    }

    uintptr_t object(uintptr_t type, size_t size = OBJECT_SIZE)
    {
        const uintptr_t o = heap.alloc(size);
        FakeHeap::put(o, offsetof(PyObject, ob_type), type);
        return o;
    }

    uint32_t slot(uint32_t level) const { return (layout.key_hash >> (level * 5)) & 0x1f; }

    uintptr_t bitmap(std::initializer_list<std::pair<uint32_t, Pair>> entries)
    {
        uint32_t bits = 0;
        for (const auto& entry : entries) {
            bits |= 1u << entry.first;
        }
        const uintptr_t node = object(layout.bitmap_type, ECHION_HAMT_BITMAP_ARRAY_OFFSET + 2 * 8 * entries.size() + 8);
        FakeHeap::put<Py_ssize_t>(node, offsetof(PyVarObject, ob_size), 2 * entries.size());
        FakeHeap::put<uint32_t>(node, offsetof(EchionHamtBitmapHeader, b_bitmap), bits);
        for (const auto& entry : entries) {
            const uint32_t index = __builtin_popcount(bits & ((1u << entry.first) - 1));
            FakeHeap::put(node, ECHION_HAMT_BITMAP_ARRAY_OFFSET + 16 * index, entry.second.key);
            FakeHeap::put(node, ECHION_HAMT_BITMAP_ARRAY_OFFSET + 16 * index + 8, entry.second.value);
        }
        return node;
    }

    uintptr_t array(uint32_t index, uintptr_t child)
    {
        const uintptr_t node = object(layout.array_type, ECHION_HAMT_ARRAY_OFFSET + 33 * 8);
        FakeHeap::put(node, ECHION_HAMT_ARRAY_OFFSET + 8 * index, child);
        return node;
    }

    uintptr_t collision(const std::vector<Pair>& pairs)
    {
        const uintptr_t node = object(layout.collision_type, ECHION_HAMT_COLLISION_ARRAY_OFFSET + 16 * pairs.size());
        FakeHeap::put<Py_ssize_t>(node, offsetof(PyVarObject, ob_size), 2 * pairs.size());
        for (size_t i = 0; i < pairs.size(); ++i) {
            FakeHeap::put(node, ECHION_HAMT_COLLISION_ARRAY_OFFSET + 16 * i, pairs[i].key);
            FakeHeap::put(node, ECHION_HAMT_COLLISION_ARRAY_OFFSET + 16 * i + 8, pairs[i].value);
        }
        return node;
    }

    // A Context whose HAMT has root as its root node.
    uintptr_t context_with_root(uintptr_t root)
    {
        const uintptr_t hamt = object(layout.hamt_type, sizeof(EchionHamtObject));
        FakeHeap::put(hamt, offsetof(EchionHamtObject, h_root), root);
        const uintptr_t ctx = object(layout.context_type, sizeof(EchionContextObject));
        FakeHeap::put(ctx, offsetof(EchionContextObject, ctx_vars), hamt);
        return ctx;
    }

    // A Context that maps the key to value in a one-entry root bitmap.
    uintptr_t context_with(uintptr_t value)
    {
        return context_with_root(bitmap({ { slot(0), { layout.key, value } } }));
    }

    uintptr_t span(uint64_t id, uint32_t span_type = 0, uintptr_t type = 0)
    {
        const uintptr_t s = object(type != 0 ? type : layout.span_data_type);
        FakeHeap::put(s, SPAN_ID, id);
        FakeHeap::put(s, SPAN_TYPE_ID, span_type);
        return s;
    }

    uintptr_t tracer_context(uint64_t id, uint64_t local_root = 0, uint32_t span_type = 0, bool reactivate = false)
    {
        const uintptr_t c = object(layout.tracer_context_type);
        FakeHeap::put(c, CTX_SPAN_ID, id);
        FakeHeap::put(c, CTX_LOCAL_ROOT, local_root);
        FakeHeap::put(c, CTX_SPAN_TYPE, span_type);
        FakeHeap::put<uint8_t>(c, CTX_REACTIVATE, reactivate ? 1 : 0);
        return c;
    }

    static void finish(uintptr_t s) { FakeHeap::put<uint8_t>(s, FINISHED, 1); }

    ContextReadStatus read(uintptr_t ctx, ContextLookupLimits limits = {}, uintptr_t* resolved = nullptr)
    {
        Lookup lookup(layout, copy, limits);
        labels = {};
        const ContextReadStatus status = lookup.read(ctx, labels, resolved);
        copies = lookup.copies();
        return status;
    }

    FakeHeap heap;
    ContextReaderLayout layout;
    const void* span_type_slot = nullptr;
    TestCopy copy;
    ContextLabels labels;
    uint32_t copies = 0;
};

TEST_F(ContextLabelsTest, BitmapRootSpan)
{
    const uintptr_t s = span(11, 3);
    uintptr_t resolved = 0;
    ASSERT_EQ(read(context_with(s), {}, &resolved), ContextReadStatus::Ok);
    EXPECT_EQ(resolved, s);
    EXPECT_EQ(labels.span_id, 11u);
    EXPECT_EQ(labels.local_root_span_id, 11u);
    EXPECT_EQ(labels.span_type_id, 3u);
    // Context, HAMT, node header, pair, value.
    EXPECT_EQ(copies, 5u);
}

TEST_F(ContextLabelsTest, MissingKeyAndNone)
{
    EXPECT_EQ(read(0), ContextReadStatus::NoLabels);
    EXPECT_EQ(read(context_with_root(bitmap({}))), ContextReadStatus::NoLabels);
    EXPECT_EQ(read(context_with_root(bitmap({ { slot(0) ^ 1, { layout.key, span(1) } } }))),
              ContextReadStatus::NoLabels);
    const uintptr_t other_key = heap.alloc(sizeof(PyObject));
    EXPECT_EQ(read(context_with_root(bitmap({ { slot(0), { other_key, span(1) } } }))), ContextReadStatus::NoLabels);
    EXPECT_EQ(read(context_with(layout.none)), ContextReadStatus::NoLabels);
    EXPECT_EQ(read(context_with_root(array(slot(0) ^ 1, bitmap({})))), ContextReadStatus::NoLabels);
}

TEST_F(ContextLabelsTest, ArrayNode)
{
    const uintptr_t child = bitmap({ { slot(1), { layout.key, span(12) } } });
    ASSERT_EQ(read(context_with_root(array(slot(0), child))), ContextReadStatus::Ok);
    EXPECT_EQ(labels.span_id, 12u);
}

TEST_F(ContextLabelsTest, CollisionNode)
{
    const uintptr_t other_key = heap.alloc(sizeof(PyObject));
    const uintptr_t node = collision({ { other_key, span(1) }, { layout.key, span(13) } });
    ASSERT_EQ(read(context_with_root(bitmap({ { slot(0), { 0, node } } }))), ContextReadStatus::Ok);
    EXPECT_EQ(labels.span_id, 13u);

    EXPECT_EQ(read(context_with_root(collision({ { other_key, span(1) } }))), ContextReadStatus::NoLabels);
}

TEST_F(ContextLabelsTest, CollisionAtMaxDepth)
{
    const uintptr_t other_key = heap.alloc(sizeof(PyObject));
    uintptr_t node = collision({ { other_key, span(1) }, { layout.key, span(14) } });
    for (int level = 6; level >= 0; --level) {
        node = bitmap({ { slot(level), { 0, node } } });
    }
    ASSERT_EQ(read(context_with_root(node)), ContextReadStatus::Ok);
    EXPECT_EQ(labels.span_id, 14u);
}

TEST_F(ContextLabelsTest, OnlyCollisionNodesAtLevel7)
{
    // A bitmap node at level 7 would need hash bits past 32.
    uintptr_t node = bitmap({ { 0, { layout.key, span(1) } } });
    for (int level = 6; level >= 0; --level) {
        node = bitmap({ { slot(level), { 0, node } } });
    }
    EXPECT_EQ(read(context_with_root(node)), ContextReadStatus::TooDeep);
}

TEST_F(ContextLabelsTest, CollisionEntryCap)
{
    std::vector<Pair> pairs;
    for (int i = 0; i < 8; ++i) {
        pairs.push_back({ heap.alloc(sizeof(PyObject)), span(1) });
    }
    pairs.back().key = layout.key;
    pairs.back().value = span(15);
    ASSERT_EQ(read(context_with_root(collision(pairs))), ContextReadStatus::Ok);
    EXPECT_EQ(labels.span_id, 15u);

    pairs.push_back({ heap.alloc(sizeof(PyObject)), span(1) });
    EXPECT_EQ(read(context_with_root(collision(pairs))), ContextReadStatus::CollisionTooLarge);
}

TEST_F(ContextLabelsTest, TypeMismatches)
{
    const uintptr_t s = span(1);

    const uintptr_t wrong_context = context_with(s);
    FakeHeap::put(wrong_context, offsetof(PyObject, ob_type), layout.hamt_type);
    EXPECT_EQ(read(wrong_context), ContextReadStatus::BadContextType);

    const uintptr_t wrong_hamt = context_with(s);
    const auto hamt = *reinterpret_cast<uintptr_t*>(wrong_hamt + offsetof(EchionContextObject, ctx_vars));
    FakeHeap::put(hamt, offsetof(PyObject, ob_type), layout.context_type);
    EXPECT_EQ(read(wrong_hamt), ContextReadStatus::BadHamtType);

    EXPECT_EQ(read(context_with_root(object(type(), 64))), ContextReadStatus::BadNode);
    EXPECT_EQ(read(context_with_root(0)), ContextReadStatus::BadNode);

    // A bitmap whose size does not match its bits.
    const uintptr_t torn = bitmap({ { slot(0), { layout.key, s } } });
    FakeHeap::put<Py_ssize_t>(torn, offsetof(PyVarObject, ob_size), 4);
    EXPECT_EQ(read(context_with_root(torn)), ContextReadStatus::BadNode);

    // A value that is neither a span nor a Context.
    EXPECT_EQ(read(context_with(object(type()))), ContextReadStatus::BadValueType);

    // A parent that is not a span.
    const uintptr_t child = span(2);
    finish(child);
    FakeHeap::put(child, PARENT, tracer_context(3));
    EXPECT_EQ(read(context_with(child)), ContextReadStatus::BadValueType);
}

TEST_F(ContextLabelsTest, SpanSubclasses)
{
    // ddtrace._trace.span.Span, from the atomic slot.
    ASSERT_EQ(read(context_with(span(21, 0, reinterpret_cast<uintptr_t>(span_type_slot)))), ContextReadStatus::Ok);
    EXPECT_EQ(labels.span_id, 21u);

    // A subclass two levels below SpanData.
    const uintptr_t subclass = type(type(layout.span_data_type));
    ASSERT_EQ(read(context_with(span(22, 0, subclass))), ContextReadStatus::Ok);
    EXPECT_EQ(labels.span_id, 22u);

    // Deeper than the tp_base walk allows.
    uintptr_t deep = layout.span_data_type;
    for (int i = 0; i < 9; ++i) {
        deep = type(deep);
    }
    EXPECT_EQ(read(context_with(span(23, 0, deep))), ContextReadStatus::BadValueType);

    // Without Span imported yet, only SpanData matches directly.
    const uintptr_t python_span = span(24, 0, reinterpret_cast<uintptr_t>(span_type_slot));
    span_type_slot = nullptr;
    EXPECT_EQ(read(context_with(python_span)), ContextReadStatus::BadValueType);
}

TEST_F(ContextLabelsTest, SubclassWalkRunsOncePerLookup)
{
    const uintptr_t subclass = type(layout.span_data_type);
    const uintptr_t parent = span(25, 0, subclass);
    const uintptr_t child = span(26, 0, subclass);
    finish(child);
    FakeHeap::put(child, PARENT, parent);
    ASSERT_EQ(read(context_with(child)), ContextReadStatus::Ok);
    EXPECT_EQ(labels.span_id, 25u);
    // Context, HAMT, node header, pair, child, child's tp_base, parent.
    EXPECT_EQ(copies, 7u);
}

TEST_F(ContextLabelsTest, UnreadableMemory)
{
    const uintptr_t s = span(1);
    const uintptr_t ctx = context_with(s);
    copy.unreadable.insert(s);
    EXPECT_EQ(read(ctx), ContextReadStatus::CopyFailed);

    copy.unreadable = { *reinterpret_cast<uintptr_t*>(ctx + offsetof(EchionContextObject, ctx_vars)) };
    EXPECT_EQ(read(ctx), ContextReadStatus::CopyFailed);

    copy.unreadable.clear();
    EXPECT_EQ(read(ctx + 1), ContextReadStatus::CopyFailed); // misaligned
    EXPECT_EQ(read(8), ContextReadStatus::CopyFailed);       // zero page
    EXPECT_EQ(copies, 0u);
}

TEST_F(ContextLabelsTest, UnreadableMemoryWithEchionCopy)
{
    const long page = sysconf(_SC_PAGESIZE);
    void* guard = mmap(nullptr, page, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    ASSERT_NE(guard, MAP_FAILED);

    const uintptr_t ctx = context_with(span(1));
    FakeHeap::put(ctx, offsetof(EchionContextObject, ctx_vars), reinterpret_cast<uintptr_t>(guard));
    EchionCopy echion_copy;
    ContextLookup<EchionCopy> lookup(layout, echion_copy);
    EXPECT_EQ(lookup.read(ctx, labels), ContextReadStatus::CopyFailed);

    // The copy recovers from the fault.
    const uintptr_t good = context_with(span(31));
    ContextLookup<EchionCopy> good_lookup(layout, echion_copy);
    ASSERT_EQ(good_lookup.read(good, labels), ContextReadStatus::Ok);
    EXPECT_EQ(labels.span_id, 31u);

    munmap(guard, page);
}

TEST_F(ContextLabelsTest, FinishedSpansResolveToParent)
{
    const uintptr_t parent = span(41, 5);
    const uintptr_t child = span(42, 6);
    finish(child);
    FakeHeap::put(child, PARENT, parent);
    uintptr_t resolved = 0;
    ASSERT_EQ(read(context_with(child), {}, &resolved), ContextReadStatus::Ok);
    EXPECT_EQ(resolved, parent);
    EXPECT_EQ(labels.span_id, 41u);

    // A finished root without a parent Context has no active span.
    finish(parent);
    EXPECT_EQ(read(context_with(child)), ContextReadStatus::NoLabels);
}

TEST_F(ContextLabelsTest, FinishedRootReactivatesParentContext)
{
    const uintptr_t root = span(51);
    finish(root);
    const uintptr_t parent_context = tracer_context(50, 49, 7, true);
    FakeHeap::put(root, PARENT_CONTEXT, parent_context);
    uintptr_t resolved = 0;
    ASSERT_EQ(read(context_with(root), {}, &resolved), ContextReadStatus::Ok);
    EXPECT_EQ(resolved, parent_context);
    EXPECT_EQ(labels.span_id, 50u);
    EXPECT_EQ(labels.local_root_span_id, 49u);
    EXPECT_EQ(labels.span_type_id, 7u);

    FakeHeap::put<uint8_t>(parent_context, CTX_REACTIVATE, 0);
    EXPECT_EQ(read(context_with(root)), ContextReadStatus::NoLabels);
}

TEST_F(ContextLabelsTest, HopAndCopyLimits)
{
    // 70 finished spans, then an unfinished one.
    uintptr_t top = span(1000);
    for (int i = 0; i < 70; ++i) {
        const uintptr_t s = span(1001 + i);
        finish(s);
        FakeHeap::put(s, PARENT, top);
        top = s;
    }
    ContextLookupLimits generous;
    generous.copies = 1000;
    EXPECT_EQ(read(context_with(top), generous), ContextReadStatus::HopLimit);
    EXPECT_EQ(read(context_with(top)), ContextReadStatus::CopyBudget);
    EXPECT_EQ(copies, ContextLookupLimits{}.copies);

    generous.resolve_hops = 70;
    ASSERT_EQ(read(context_with(top), generous), ContextReadStatus::Ok);
    EXPECT_EQ(labels.span_id, 1000u);
}

TEST_F(ContextLabelsTest, LocalRootLabels)
{
    const uintptr_t root = span(61, 8);
    const uintptr_t child = span(62, 9);
    FakeHeap::put(child, LOCAL_ROOT, root);
    FakeHeap::put(child, PARENT, root);
    FakeHeap::put<uint64_t>(child, PARENT_ID, 61);
    ASSERT_EQ(read(context_with(child)), ContextReadStatus::Ok);
    EXPECT_EQ(labels.span_id, 62u);
    EXPECT_EQ(labels.local_root_span_id, 61u);
    EXPECT_EQ(labels.span_type_id, 8u); // the local root's type
}

TEST_F(ContextLabelsTest, DistributedChildLabels)
{
    // A local root continuing a remote trace: no parent span, a parent id, and the remote Context.
    const uintptr_t child = span(71, 4);
    FakeHeap::put<uint64_t>(child, PARENT_ID, 70);
    const uintptr_t remote = tracer_context(70, 69, 3);
    FakeHeap::put(child, PARENT_CONTEXT, remote);

    ASSERT_EQ(read(context_with(child)), ContextReadStatus::Ok);
    EXPECT_EQ(labels.span_id, 71u);
    EXPECT_EQ(labels.local_root_span_id, 69u); // propagated local root
    EXPECT_EQ(labels.span_type_id, 3u);        // propagated span type

    // Without propagated meta: the Context's span id, and the local root's type.
    FakeHeap::put<uint64_t>(remote, CTX_LOCAL_ROOT, 0);
    FakeHeap::put<uint32_t>(remote, CTX_SPAN_TYPE, 0);
    ASSERT_EQ(read(context_with(child)), ContextReadStatus::Ok);
    EXPECT_EQ(labels.local_root_span_id, 70u);
    EXPECT_EQ(labels.span_type_id, 4u);

    // A Context without a span id falls back to the local root.
    FakeHeap::put<uint64_t>(remote, CTX_SPAN_ID, 0);
    ASSERT_EQ(read(context_with(child)), ContextReadStatus::Ok);
    EXPECT_EQ(labels.local_root_span_id, 71u);

    // A span with a parent Context but no parent id is not a distributed child.
    FakeHeap::put<uint64_t>(remote, CTX_LOCAL_ROOT, 69);
    FakeHeap::put<uint64_t>(child, PARENT_ID, 0);
    ASSERT_EQ(read(context_with(child)), ContextReadStatus::Ok);
    EXPECT_EQ(labels.local_root_span_id, 71u);
}

TEST_F(ContextLabelsTest, ContextValueLabels)
{
    ASSERT_EQ(read(context_with(tracer_context(81, 80, 2))), ContextReadStatus::Ok);
    EXPECT_EQ(labels.span_id, 81u);
    EXPECT_EQ(labels.local_root_span_id, 80u);
    EXPECT_EQ(labels.span_type_id, 2u);

    ASSERT_EQ(read(context_with(tracer_context(82))), ContextReadStatus::Ok);
    EXPECT_EQ(labels.local_root_span_id, 82u);
    EXPECT_EQ(labels.span_type_id, 0u);
}

TEST_F(ContextLabelsTest, SpanIdZeroHasNoLabels)
{
    EXPECT_EQ(read(context_with(span(0))), ContextReadStatus::NoLabels);
    EXPECT_EQ(read(context_with(tracer_context(0, 5))), ContextReadStatus::NoLabels);
}

TEST_F(ContextLabelsTest, ValueHeaderFirstWhenNoSingleCopyFits)
{
    layout.value_copy_size = 0;
    ASSERT_EQ(read(context_with(span(91))), ContextReadStatus::Ok);
    EXPECT_EQ(labels.span_id, 91u);
    EXPECT_EQ(copies, 6u); // the value takes a header copy and a field copy
}

// Live CPython Contexts, read through the calibrated layout and Echion's copy.
class LiveContextTest : public ::testing::Test
{
  protected:
    static void SetUpTestSuite() { Py_Initialize(); }

    void SetUp() override { ASSERT_EQ(calibrate_context_layout(layout), nullptr); }

    // Sets var to value in ctx.
    static void set(PyObject* ctx, PyObject* var, PyObject* value)
    {
        ASSERT_EQ(PyContext_Enter(ctx), 0);
        PyObject* token = PyContextVar_Set(var, value);
        ASSERT_NE(token, nullptr);
        Py_DECREF(token);
        ASSERT_EQ(PyContext_Exit(ctx), 0);
    }

    static PyObject* var_with_hash(Py_hash_t hash)
    {
        PyObject* var = PyContextVar_New("test", nullptr);
        reinterpret_cast<EchionContextVarObject*>(var)->var_hash = hash;
        return var;
    }

    ContextReadStatus find(PyObject* ctx, PyObject* var, uintptr_t& value, ContextLookupLimits limits = {})
    {
        ContextReaderLayout keyed = layout;
        keyed.key = reinterpret_cast<uintptr_t>(var);
        keyed.key_hash = hamt_hash(PyObject_Hash(var));
        EchionCopy echion_copy;
        ContextLookup<EchionCopy> lookup(keyed, echion_copy, limits);
        return lookup.find_value(reinterpret_cast<uintptr_t>(ctx), value);
    }

    ContextReaderLayout layout;
};

TEST_F(LiveContextTest, CalibratesNodeTypes)
{
    EXPECT_STREQ(reinterpret_cast<PyTypeObject*>(layout.hamt_type)->tp_name, "hamt");
    EXPECT_STREQ(reinterpret_cast<PyTypeObject*>(layout.bitmap_type)->tp_name, "hamt_bitmap_node");
    EXPECT_STREQ(reinterpret_cast<PyTypeObject*>(layout.array_type)->tp_name, "hamt_array_node");
    EXPECT_STREQ(reinterpret_cast<PyTypeObject*>(layout.collision_type)->tp_name, "hamt_collision_node");
}

TEST_F(LiveContextTest, FindsEveryVariable)
{
    PyObject* ctx = PyContext_New();
    std::vector<PyObject*> vars;
    std::vector<PyObject*> values;
    for (int i = 0; i < 200; ++i) {
        vars.push_back(PyContextVar_New("test", nullptr));
        values.push_back(PyLong_FromLong(5000000 + i));
        set(ctx, vars.back(), values.back());
    }
    PyObject* missing = PyContextVar_New("test", nullptr);

    for (size_t i = 0; i < vars.size(); ++i) {
        uintptr_t value = 0;
        ASSERT_EQ(find(ctx, vars[i], value), ContextReadStatus::Ok);
        EXPECT_EQ(value, reinterpret_cast<uintptr_t>(values[i]));
    }
    uintptr_t value = 1;
    ASSERT_EQ(find(ctx, missing, value), ContextReadStatus::Ok);
    EXPECT_EQ(value, 0u);

    for (size_t i = 0; i < vars.size(); ++i) {
        Py_DECREF(vars[i]);
        Py_DECREF(values[i]);
    }
    Py_DECREF(missing);
    Py_DECREF(ctx);
}

TEST_F(LiveContextTest, DeepTreeAndCollisions)
{
    // Hashes that share their low 30 bits split only at level 6, and equal hashes share a collision
    // node at level 7.
    PyObject* ctx = PyContext_New();
    const Py_hash_t base = 0x0123abcd & ((1 << 30) - 1);
    std::vector<PyObject*> vars = {
        var_with_hash(base),
        var_with_hash(base | (1 << 30)),
        var_with_hash(base | (1 << 30)),
        var_with_hash(base | (1 << 30)),
    };
    std::vector<PyObject*> values;
    for (size_t i = 0; i < vars.size(); ++i) {
        values.push_back(PyLong_FromLong(6000000 + static_cast<long>(i)));
        set(ctx, vars[i], values[i]);
    }

    for (size_t i = 0; i < vars.size(); ++i) {
        uintptr_t value = 0;
        ASSERT_EQ(find(ctx, vars[i], value), ContextReadStatus::Ok) << i;
        EXPECT_EQ(value, reinterpret_cast<uintptr_t>(values[i])) << i;
    }

    // The collision node is at level 7: one level fewer does not reach it.
    ContextLookupLimits shallow;
    shallow.hamt_levels = 7;
    uintptr_t value = 0;
    EXPECT_EQ(find(ctx, vars[3], value, shallow), ContextReadStatus::TooDeep);

    for (size_t i = 0; i < vars.size(); ++i) {
        Py_DECREF(vars[i]);
        Py_DECREF(values[i]);
    }
    Py_DECREF(ctx);
}

TEST_F(LiveContextTest, ReadLabelsNeedsAnEnabledReader)
{
    // This binary does not load the tracer, so init_context_reader() was never called.
    ASSERT_FALSE(context_reader().enabled.load());
    PyObject* ctx = PyContext_New();
    EXPECT_FALSE(read_labels(reinterpret_cast<PyContext*>(ctx)).has_value());
    Py_DECREF(ctx);
}

TEST_F(LiveContextTest, RejectsOtherObjects)
{
    PyObject* not_a_context = PyDict_New();
    PyObject* var = PyContextVar_New("test", nullptr);
    uintptr_t value = 0;
    EXPECT_EQ(find(not_a_context, var, value), ContextReadStatus::BadContextType);
    EXPECT_EQ(PyErr_Occurred(), nullptr);
    Py_DECREF(var);
    Py_DECREF(not_a_context);
}

} // namespace

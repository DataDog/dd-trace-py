#pragma once

#include <string>
#include <unordered_map>
#include <unordered_set>

using namespace std;

struct GlobalContext
{
    // TODO FIXME: use size_t key once the Python usages are removed
    unordered_map<string, string> framework_data;

    GlobalContext() = default;

    void reset_global_data();
};
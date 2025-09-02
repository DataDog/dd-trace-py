#include "application_context.h"

#include <sstream>

using namespace std;

std::string ApplicationContext::get_context_id() const {
    const char* ptr = nullptr;
    Py_ssize_t len = 0;
    if (get_current_context_id_utf8(&ptr, &len) && ptr && len > 0) {
        return std::string(ptr, static_cast<size_t>(len));
    }
    return std::string();
}

TaintRangeMapTypePtr ApplicationContext::create_context_map() {
    const char* ptr = nullptr;
    Py_ssize_t len = 0;
    auto map_ptr = make_shared<TaintRangeMapType>();
    if (!get_current_context_id_utf8(&ptr, &len) || !ptr || len <= 0) {
        // Not tracked when no valid context id
        return map_ptr;
    }
    const std::string ctx_id(ptr, static_cast<size_t>(len));

    std::unique_lock<std::shared_mutex> lock(context_mutex);
    if (context_maps.find(ctx_id) == context_maps.end()) {
        context_order.push(ctx_id);
    }
    context_maps[ctx_id] = map_ptr;

    enforce_max_size();
    return map_ptr;
}

TaintRangeMapTypePtr ApplicationContext::get_context_map() {
    const char* ptr = nullptr;
    Py_ssize_t len = 0;
    if (!get_current_context_id_utf8(&ptr, &len) || !ptr || len <= 0) {
        return nullptr;
    }
    return get_context_map(std::string(ptr, static_cast<size_t>(len)));
}

TaintRangeMapTypePtr ApplicationContext::get_context_map(const std::string &ctx_id) {
    if (ctx_id.empty()) {
        return nullptr;
    }
    std::shared_lock<std::shared_mutex> rlock(context_mutex);
    auto it = context_maps.find(ctx_id);
    if (it != context_maps.end()) {
        return it->second;
    }
    return nullptr;
}

void ApplicationContext::clear_tainting_map(const TaintRangeMapTypePtr &tx_map) {
    if (!tx_map) {
        return;
    }
    std::unique_lock<std::shared_mutex> lock(context_mutex);
    for (auto it = context_maps.begin(); it != context_maps.end(); ++it) {
        if (it->second == tx_map) {
            if (it->second) {
                it->second->clear();
            }
            context_maps.erase(it);
            break;
        }
    }
}

void ApplicationContext::clear_tainting_maps() {
    std::unique_lock<std::shared_mutex> lock(context_mutex);
    for (auto &kv : context_maps) {
        if (kv.second) {
            kv.second->clear();
        }
    }
    context_maps.clear();
    while (!context_order.empty()) context_order.pop();
}

std::string ApplicationContext::create_context() {
    const char* ptr = nullptr;
    Py_ssize_t len = 0;
    if (!get_current_context_id_utf8(&ptr, &len) || !ptr || len <= 0) {
        return std::string();
    }
    const std::string ctx_id(ptr, static_cast<size_t>(len));
    {
        std::unique_lock<std::shared_mutex> lock(context_mutex);
        if (context_maps.find(ctx_id) == context_maps.end()) {
            context_order.push(ctx_id);
            context_maps[ctx_id] = make_shared<TaintRangeMapType>();
            enforce_max_size();
        }
    }
    return ctx_id;
}

void ApplicationContext::reset_context() {
    const char* ptr = nullptr;
    Py_ssize_t len = 0;
    if (!get_current_context_id_utf8(&ptr, &len) || !ptr || len <= 0) {
        return;
    }
    const std::string ctx_id(ptr, static_cast<size_t>(len));

    std::unique_lock<std::shared_mutex> lock(context_mutex);
    context_maps.erase(ctx_id);
    enforce_max_size();
}

void ApplicationContext::enforce_max_size() {
    while (context_maps.size() > MAX_SIZE && !context_order.empty()) {
        const auto &oldest = context_order.front();
        context_maps.erase(oldest);
        context_order.pop();
    }
}

void pyexport_application_context(py::module &m) {
    // Keep get_context_id available (returns current id as string)
    m.def("get_context_id", [] { return application_context->get_context_id(); });
    // Allow Python to set the native ContextVar for benchmarking convenience
    m.def("set_context_id", [](const std::string& s) {
        py::gil_scoped_acquire gil;
        ensure_iast_ctxvar_created();
        if (!g_iast_ctxvar) return;
        PyObject* py_s = PyUnicode_FromStringAndSize(s.data(), static_cast<Py_ssize_t>(s.size()));
        if (!py_s) {
            PyErr_Clear();
            return;
        }
        PyObject* token = PyContextVar_Set(g_iast_ctxvar, py_s);
        Py_DECREF(py_s);
        Py_XDECREF(token);
    });
    m.def("clear_tainting_maps", [] { application_context->clear_tainting_maps(); });
    // Returns context id
    m.def("create_context", [] { return application_context->create_context(); });
    m.def("reset_context", [] { application_context->reset_context(); });
    // Expose only parameterized get_context_map to Python (for benchmarking explicit id)
    m.def("get_context_map", [](const std::string &ctx_id) { return application_context->get_context_map(ctx_id) != nullptr; });
    // Benchmark helpers (no type exposure required)
    m.def("create_context_map_bench", [] { (void)application_context->create_context_map(); });
    m.def("get_context_map_bench", [](const std::string &ctx_id) { (void)application_context->get_context_map(ctx_id); });
}

std::unique_ptr<ApplicationContext> application_context;

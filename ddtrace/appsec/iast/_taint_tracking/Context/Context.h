#pragma once

#include <pybind11/pybind11.h>

#include <functional>
#include <map>
#include <string>
#include <tuple>
#include <unordered_set>
#include <utility>
#include <vector>

using namespace std;
namespace py = pybind11;

struct PyIntHasher
{
    long long operator()(const py::int_& key) const { return py::hash(key); }
};

struct Context
{
  private:
    bool blocked = false;
    bool propagation = false;
    //    unordered_set<EvidenceDTO, EvidenceDTO::hash_fn> evidences;
    // TODO: move to native int once this isn't used in Python
    unordered_set<size_t> blocking_vulnerabilities_sent_hashes;
    // TODO: Move to shared_ptr<StackTraceElement> once this isn't used in Python

    static size_t pyint_and_analyzer_hash(const py::int_& vuln_hash, string_view analyzer_name)
    {
        return std::hash<size_t>()(std::hash<long long>()(PyIntHasher()(vuln_hash)) +
                                   std::hash<string_view>()(analyzer_name));
    }

  public:
    Context();

    // As I understand it, this should be done automatically once Context is
    // deleted... but if I don't do it, the interpreter gives an ugly sigsegv at
    // the end...
    bool is_request_blocked() const { return blocked; }

    void block_request() { blocked = true; }

    void set_propagation(bool set) { propagation = set; }

    bool get_propagation() const { return propagation; }

    void add_sent_blocking_vulnerability_hash(const py::int_& vuln_hash, string_view analyzer_name)
    {
        blocking_vulnerabilities_sent_hashes.insert(pyint_and_analyzer_hash(vuln_hash, analyzer_name));
    }

    bool blocking_vulnerability_hash_already_sent(const py::int_& vuln_hash, string_view analyzer_name) const
    {
        auto res_hash = pyint_and_analyzer_hash(vuln_hash, analyzer_name);
        return blocking_vulnerabilities_sent_hashes.find(res_hash) != blocking_vulnerabilities_sent_hashes.end();
    }

    void reset_blocking_vulnerability_hashes() { blocking_vulnerabilities_sent_hashes.clear(); }
};

void
pyexport_context(py::module& m);

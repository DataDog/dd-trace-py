#pragma once

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "Context/Context.h"
#include "Context/GlobalContext.h"
#include "Exceptions/exceptions.h"
#include "TaintedObject/TaintedObject.h"
#include "TaintRange/TaintRange.h"
#include "absl/container/node_hash_map.h"

#include <stack>
#include <unordered_map>
#include <unordered_set>

using namespace std;

namespace py = pybind11;

class Initializer {
private:
    bool settings_loaded = false;
    bool taint_debug = false;

    // Used to reset stdlib_modules to these values
    unordered_set<string> stdlib_modules_orig{
        "__future__",  "__phello__",  "abc",       "aifc",        "antigravity", "argparse",     "ast",
        "asynchat",    "asyncore",    "base64",    "bdb",         "binhex",      "bisect",       "cProfile",
        "calendar",    "cgi",         "cgitb",     "chunk",       "cmd",         "code",         "codecs",
        "codeop",      "collections", "colorsys",  "compileall",  "contextlib",  "copy",         "csv",
        "ctypes",      "curses",      "decimal",   "difflib",     "dis",         "distutils",    "dummy_threading",
        "email",       "encodings",   "ensurepip", "filecmp",     "fileinput",   "fnmatch",      "formatter",
        "fractions",   "ftplib",      "functools", "genericpath", "getopt",      "getpass",      "gettext",
        "glob",        "gzip",        "hashlib",   "heapq",       "hmac",        "idlelib",      "imaplib",
        "imghdr",      "importlib",   "inspect",   "io",          "json",        "keyword",      "linecache",
        "locale",      "logging",     "mailbox",   "mailcap",     "mimetypes",   "modulefinder", "multiprocessing",
        "netrc",       "nntplib",     "ntpath",    "nturl2path",  "numbers",     "opcode",       "optparse",
        "os",          "pdb",         "pickle",    "pickletools", "pipes",       "pkgutil",      "platform",
        "plistlib",    "poplib",      "posixpath", "pprint",      "profile",     "pstats",       "pty",
        "py_compile",  "pyclbr",      "pydoc",     "pydoc_data",  "quopri",      "random",       "re",
        "rlcompleter", "runpy",       "sched",     "shelve",      "shlex",       "shutil",       "site",
        "smtpd",       "smtplib",     "sndhdr",    "socket",      "sqlite3",     "sre_compile",  "sre_constants",
        "sre_parse",   "ssl",         "stat",      "string",      "stringprep",  "struct",       "subprocess",
        "sunau",       "symbol",      "symtable",  "sysconfig",   "tabnanny",    "tarfile",      "telnetlib",
        "tempfile",    "textwrap",    "this",      "threading",   "timeit",      "token",        "tokenize",
        "trace",       "traceback",   "tty",       "types",       "urllib",      "uu",           "uuid",
        "warnings",    "wave",        "weakref",   "webbrowser",  "wsgiref",     "xdrlib",       "xml",
        "zipfile",
    };
    py::object pyfunc_get_settings;
    py::object pyfunc_get_python_lib;
    unordered_map<size_t, shared_ptr<Context>> contexts;
    static constexpr int TAINTRANGES_STACK_SIZE = 4096;
    static constexpr int TAINTEDOBJECTS_STACK_SIZE = 4096;
    static constexpr int SOURCE_STACK_SIZE = 1024;
    stack<TaintedObjectPtr> available_taintedobjects_stack;
    stack<TaintRangePtr> available_ranges_stack;
    stack<SourcePtr> available_source_stack;
    unordered_set<TaintRangeMapType*> active_map_addreses;
    absl::node_hash_map<size_t, SourcePtr> allocated_sources_map;

public:
    // Imported Python stuff
    py::object pytype_frame;
    unordered_set<string> no_stdlib_modules;
    unordered_set<string> stdlib_paths;
    unordered_set<string> site_package_paths;
    unique_ptr<GlobalContext> global_context;

    Initializer();

    void load_modules();

    void load_local_settings(bool allow_skip);

    bool get_taint_debug();

    void set_taint_debug(bool);

    TaintRangeMapType* create_tainting_map();

    void free_tainting_map(TaintRangeMapType* tx_map);

    TaintRangeMapType* get_tainting_map();

    void clear_tainting_maps();

    void get_paths();

    void reset_stdlib_paths_and_modules();

    unordered_set<string> stdlib_modules{stdlib_modules_orig};

    shared_ptr<Context> create_context();

    void destroy_context();

    shared_ptr<Context> get_context(size_t tx_id = 0);

    void contexts_reset();

    static size_t context_id();

    bool get_propagation();

    // IMPORTANT: if the returned object is not assigned to the map, you have responsability of
    // calling release_tainted_object on it or you'll have a leak.
    TaintedObjectPtr allocate_tainted_object();

    TaintedObjectPtr allocate_tainted_object(TaintRangeRefs ranges) {
        auto toptr = allocate_tainted_object();
        toptr->set_values(move(ranges));
        return toptr;
    }

    TaintedObjectPtr allocate_tainted_object_copy(const TaintRangeRefs& ranges) {
        auto toptr = allocate_tainted_object();
        toptr->copy_values(ranges);
        return toptr;
    }

    TaintedObjectPtr allocate_tainted_object(TaintedObjectPtr from) {
        if (!from) {
            return allocate_tainted_object();
        }
        return allocate_tainted_object(move(from->ranges_));
    }

    TaintedObjectPtr allocate_tainted_object_copy(const TaintedObjectPtr& from) {
        if (!from) {
            return allocate_tainted_object();
        }
        return allocate_tainted_object_copy(from->ranges_);
    }

    void release_tainted_object(TaintedObjectPtr tobj);

    // IMPORTANT: if the returned object is not assigned to the map, you have responsibility of
    // calling release_taint_range on it or you'll have a leak.
    TaintRangePtr allocate_taint_range(int start, int lenght, SourcePtr origin);
    static SourcePtr reuse_taint_origin(SourcePtr origin);

    void release_taint_range(TaintRangePtr rangeptr);

    // IMPORTANT: if the returned object is not assigned to a range, you have responsibility of
    // calling release_taint_origin on it or you'll have a leak.
    SourcePtr allocate_taint_origin(string, OriginType, string);

    void release_taint_origin(SourcePtr);
};

extern unique_ptr<Initializer> initializer;

void pyexport_initializer(py::module& m);

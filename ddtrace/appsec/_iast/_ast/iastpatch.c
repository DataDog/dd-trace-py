#include <Python.h>
#include <ctype.h>
#include <stdlib.h>
#include <string.h>

/* --- Global Vars --- */
static char** builtins_denylist = NULL;
static size_t builtins_denylist_count = 0;

static char** user_allowlist = NULL;
static size_t user_allowlist_count = 0;
static char** user_denylist = NULL;
static size_t user_denylist_count = 0;

/* --- Global Cache for packages_distributions --- */
static char** cached_packages = NULL;
static size_t cached_packages_count = 0;

/* Static Lists */
static size_t static_allowlist_count = 5;
static const char* static_allowlist[] = { "jinja2.", "pygments.", "multipart.", "sqlalchemy.", "python_multipart." };

static size_t static_denylist_count = 145;
static const char* static_denylist[] = { "django.apps.config.",
                                         "django.apps.registry.",
                                         "django.conf.",
                                         "django.contrib.admin.actions.",
                                         "django.contrib.admin.admin.",
                                         "django.contrib.admin.apps.",
                                         "django.contrib.admin.checks.",
                                         "django.contrib.admin.decorators.",
                                         "django.contrib.admin.exceptions.",
                                         "django.contrib.admin.helpers.",
                                         "django.contrib.admin.image_formats.",
                                         "django.contrib.admin.options.",
                                         "django.contrib.admin.sites.",
                                         "django.contrib.admin.templatetags.",
                                         "django.contrib.admin.views.autocomplete.",
                                         "django.contrib.admin.views.decorators.",
                                         "django.contrib.admin.views.main.",
                                         "django.contrib.admin.wagtail_hooks.",
                                         "django.contrib.admin.widgets.",
                                         "django.contrib.admindocs.utils.",
                                         "django.contrib.admindocs.views.",
                                         "django.contrib.auth.admin.",
                                         "django.contrib.auth.apps.",
                                         "django.contrib.auth.backends.",
                                         "django.contrib.auth.base_user.",
                                         "django.contrib.auth.checks.",
                                         "django.contrib.auth.context_processors.",
                                         "django.contrib.auth.decorators.",
                                         "django.contrib.auth.hashers.",
                                         "django.contrib.auth.image_formats.",
                                         "django.contrib.auth.management.",
                                         "django.contrib.auth.middleware.",
                                         "django.contrib.auth.password_validation.",
                                         "django.contrib.auth.signals.",
                                         "django.contrib.auth.templatetags.",
                                         "django.contrib.auth.validators.",
                                         "django.contrib.auth.wagtail_hooks.",
                                         "django.contrib.contenttypes.admin.",
                                         "django.contrib.contenttypes.apps.",
                                         "django.contrib.contenttypes.checks.",
                                         "django.contrib.contenttypes.fields.",
                                         "django.contrib.contenttypes.forms.",
                                         "django.contrib.contenttypes.image_formats.",
                                         "django.contrib.contenttypes.management.",
                                         "django.contrib.contenttypes.models.",
                                         "django.contrib.contenttypes.templatetags.",
                                         "django.contrib.contenttypes.views.",
                                         "django.contrib.contenttypes.wagtail_hooks.",
                                         "django.contrib.humanize.templatetags.",
                                         "django.contrib.messages.admin.",
                                         "django.contrib.messages.api.",
                                         "django.contrib.messages.apps.",
                                         "django.contrib.messages.constants.",
                                         "django.contrib.messages.context_processors.",
                                         "django.contrib.messages.image_formats.",
                                         "django.contrib.messages.middleware.",
                                         "django.contrib.messages.storage.",
                                         "django.contrib.messages.templatetags.",
                                         "django.contrib.messages.utils.",
                                         "django.contrib.messages.wagtail_hooks.",
                                         "django.contrib.sessions.admin.",
                                         "django.contrib.sessions.apps.",
                                         "django.contrib.sessions.backends.",
                                         "django.contrib.sessions.base_session.",
                                         "django.contrib.sessions.exceptions.",
                                         "django.contrib.sessions.image_formats.",
                                         "django.contrib.sessions.middleware.",
                                         "django.contrib.sessions.templatetags.",
                                         "django.contrib.sessions.wagtail_hooks.",
                                         "django.contrib.sites.",
                                         "django.contrib.staticfiles.admin.",
                                         "django.contrib.staticfiles.apps.",
                                         "django.contrib.staticfiles.checks.",
                                         "django.contrib.staticfiles.finders.",
                                         "django.contrib.staticfiles.image_formats.",
                                         "django.contrib.staticfiles.models.",
                                         "django.contrib.staticfiles.storage.",
                                         "django.contrib.staticfiles.templatetags.",
                                         "django.contrib.staticfiles.utils.",
                                         "django.contrib.staticfiles.wagtail_hooks.",
                                         "django.core.cache.backends.",
                                         "django.core.cache.utils.",
                                         "django.core.checks.async_checks.",
                                         "django.core.checks.caches.",
                                         "django.core.checks.compatibility.",
                                         "django.core.checks.compatibility.django_4_0.",
                                         "django.core.checks.database.",
                                         "django.core.checks.files.",
                                         "django.core.checks.messages.",
                                         "django.core.checks.model_checks.",
                                         "django.core.checks.registry.",
                                         "django.core.checks.security.",
                                         "django.core.checks.security.base.",
                                         "django.core.checks.security.csrf.",
                                         "django.core.checks.security.sessions.",
                                         "django.core.checks.templates.",
                                         "django.core.checks.translation.",
                                         "django.core.checks.urls",
                                         "django.core.exceptions.",
                                         "django.core.mail.",
                                         "django.core.management.base.",
                                         "django.core.management.color.",
                                         "django.core.management.sql.",
                                         "django.core.paginator.",
                                         "django.core.signing.",
                                         "django.core.validators.",
                                         "django.dispatch.dispatcher.",
                                         "django.template.autoreload.",
                                         "django.template.backends.",
                                         "django.template.base.",
                                         "django.template.context.",
                                         "django.template.context_processors.",
                                         "django.template.defaultfilters.",
                                         "django.template.defaulttags.",
                                         "django.template.engine.",
                                         "django.template.exceptions.",
                                         "django.template.library.",
                                         "django.template.loader.",
                                         "django.template.loader_tags.",
                                         "django.template.loaders.",
                                         "django.template.response.",
                                         "django.template.smartif.",
                                         "django.template.utils.",
                                         "django.templatetags.",
                                         "django.test.",
                                         "django.urls.base.",
                                         "django.urls.conf.",
                                         "django.urls.converters.",
                                         "django.urls.exceptions.",
                                         "django.urls.resolvers.",
                                         "django.urls.utils.",
                                         "django.utils.",
                                         "django_filters.compat.",
                                         "django_filters.conf.",
                                         "django_filters.constants.",
                                         "django_filters.exceptions.",
                                         "django_filters.fields.",
                                         "django_filters.filters.",
                                         "django_filters.filterset.",
                                         "django_filters.rest_framework.",
                                         "django_filters.rest_framework.backends.",
                                         "django_filters.rest_framework.filters.",
                                         "django_filters.rest_framework.filterset.",
                                         "django_filters.utils.",
                                         "django_filters.widgets." };

static size_t static_stdlib_count = 216;
static const char* static_stdlib_denylist[] = {
    "__future__",
    "_ast",
    "_compression",
    "_thread",
    "abc",
    "aifc",
    "argparse",
    "array",
    "ast",
    "asynchat",
    "asyncio",
    "asyncore",
    "atexit",
    "audioop",
    "base64",
    "bdb",
    "binascii",
    "bisect",
    "builtins",
    "bz2",
    "cProfile",
    "calendar",
    "cgi",
    "cgitb",
    "chunk",
    "cmath",
    "cmd",
    "code",
    "codecs",
    "codeop",
    "collections",
    "colorsys",
    "compileall",
    "concurrent",
    "configparser",
    "contextlib",
    "contextvars",
    "copy",
    "copyreg",
    "crypt",
    "csv",
    "ctypes",
    "curses",
    "dataclasses",
    "datetime",
    "dbm",
    "decimal",
    "difflib",
    "dis",
    "distutils",
    "doctest",
    "email",
    "encodings",
    "ensurepip",
    "enum",
    "errno",
    "faulthandler",
    "fcntl",
    "filecmp",
    "fileinput",
    "fnmatch",
    "fractions",
    "ftplib",
    "functools",
    "gc",
    "getopt",
    "getpass",
    "gettext",
    "glob",
    "graphlib",
    "grp",
    "gzip",
    "hashlib",
    "heapq",
    "hmac",
    "html",
    "http",
    "idlelib",
    "imaplib",
    "imghdr",
    "imp",
    "importlib",
    "inspect",
    "io",
    "_io",
    "ipaddress",
    "itertools",
    "json",
    "keyword",
    "lib2to3",
    "linecache",
    "locale",
    "logging",
    "lzma",
    "mailbox",
    "mailcap",
    "marshal",
    "math",
    "mimetypes",
    "mmap",
    "modulefinder",
    "msilib",
    "msvcrt",
    "multiprocessing",
    "netrc",
    "nis",
    "nntplib",
    "ntpath",
    "numbers",
    "opcode",
    "operator",
    "optparse",
    "os",
    "ossaudiodev",
    "pathlib",
    "pdb",
    "pickle",
    "pickletools",
    "pipes",
    "pkgutil",
    "platform",
    "plistlib",
    "poplib",
    "posix",
    "posixpath",
    "pprint",
    "profile",
    "pstats",
    "pty",
    "pwd",
    "py_compile",
    "pyclbr",
    "pydoc",
    "queue",
    "quopri",
    "random",
    "re",
    "readline",
    "reprlib",
    "resource",
    "rlcompleter",
    "runpy",
    "sched",
    "secrets",
    "select",
    "selectors",
    "shelve",
    "shlex",
    "shutil",
    "signal",
    "site",
    "smtpd",
    "smtplib",
    "sndhdr",
    "socket",
    "socketserver",
    "spwd",
    "sqlite3",
    "sre",
    "sre_compile",
    "sre_constants",
    "sre_parse",
    "ssl",
    "stat",
    "statistics",
    "string",
    "stringprep",
    "struct",
    "subprocess",
    "sunau",
    "symtable",
    "sys",
    "sysconfig",
    "syslog",
    "tabnanny",
    "tarfile",
    "telnetlib",
    "tempfile",
    "termios",
    "test",
    "textwrap",
    "threading",
    "time",
    "timeit",
    "tkinter",
    "token",
    "tokenize",
    "tomllib",
    "trace",
    "traceback",
    "tracemalloc",
    "tty",
    "turtle",
    "turtledemo",
    "types",
    "typing",
    "unicodedata",
    "unittest",
    "uu",
    "uuid",
    "venv",
    "warnings",
    "wave",
    "weakref",
    "webbrowser",
    "winreg",
    "winsound",
    "wsgiref",
    "xdrlib",
    "xml",
    "xmlrpc",
    "zipapp",
    "zipfile",
    "zipimport",
    "zlib",
    "zoneinfo",
};

/* --- Helper function: str_in_list ---
   Returns 1 (true) if string is in a list of chars.
*/
static int
str_in_list(const char* needle, const char** list, size_t count)
{
    for (size_t i = 0; i < count; i++) {
        if (strncmp(needle, list[i], strlen(list[i])) == 0) {
            return 1;
        }
    }
    return 0;
}

/* --- Helper function to extract and lowercase the first part of the module name --- */
void
get_first_part_lower(const char* module_name, char* first_part, size_t max_len)
{
    if (!module_name || !first_part || max_len == 0) {
        if (first_part && max_len > 0) {
            first_part[0] = '\0';
        }
        return;
    }

    const char* dot = strchr(module_name, '.');
    size_t len = dot ? (size_t)(dot - module_name) : strlen(module_name);

    if (len >= max_len) {
        len = max_len - 1;
    }

    strncpy(first_part, module_name, len);
    first_part[len] = '\0';

    for (size_t i = 0; i < len; i++) {
        first_part[i] = (char)tolower((unsigned char)first_part[i]);
    }
}

/* --- Helper function: is_first_party ---
   Returns 1 (true) if the module is considered first-party, 0 otherwise.
   It calls importlib.metadata.packages_distributions only once,
   caches the resulting package names (as lowercase C strings),
   and then compares the first component of the given module name against that list.
*/
static int
is_first_party(const char* module_name)
{
    // If the module name contains "vendor." or "vendored.", return false.
    if (strstr(module_name, "vendor.") || strstr(module_name, "vendored.")) {
        return 0;
    }

    // If the packages list is not cached, call packages_distributions and cache its result.
    if (cached_packages == NULL) {
        PyObject* metadata;
        if (PY_VERSION_HEX < 0x030A0000) { // Python < 3.10
            metadata = PyImport_ImportModule("importlib_metadata");
        } else {
            metadata = PyImport_ImportModule("importlib.metadata");
        }

        if (!metadata) {
            return 0;
        }

        PyObject* func = PyObject_GetAttrString(metadata, "packages_distributions");
        Py_DECREF(metadata);
        if (!func) {
            return 0;
        }
        PyObject* result = PyObject_CallObject(func, NULL);
        Py_DECREF(func);
        if (!result)
            return 0;

        // Convert the result to a fast sequence (e.g., list or tuple).
        PyObject* fast = PySequence_Fast(result, "expected a sequence");
        Py_DECREF(result);
        if (!fast)
            return 0;
        Py_ssize_t n = PySequence_Fast_GET_SIZE(fast);
        cached_packages = malloc(n * sizeof(char*));
        if (!cached_packages) {
            Py_DECREF(fast);
            return 0;
        }
        cached_packages_count = (size_t)n;
        for (Py_ssize_t i = 0; i < n; i++) {
            PyObject* item = PySequence_Fast_GET_ITEM(fast, i); // Borrowed reference.
            if (!PyUnicode_Check(item)) {
                cached_packages[i] = NULL;
            } else {
                const char* s = PyUnicode_AsUTF8(item);
                if (s) {
                    char* dup = strdup(s);
                    if (dup) {
                        // Convert to lowercase.
                        for (char* p = dup; *p; p++) {
                            *p = tolower(*p);
                        }
                        cached_packages[i] = dup;
                    } else {
                        cached_packages[i] = NULL;
                    }
                } else {
                    cached_packages[i] = NULL;
                }
            }
        }
        Py_DECREF(fast);
    }

    // Extract the first component from module_name (up to the first dot) and convert it to lowercase.
    char first_part[256];
    get_first_part_lower(module_name, first_part, sizeof(first_part));

    // If the first part is found in the cached packages, it's not a first-party module.
    for (size_t i = 0; i < cached_packages_count; i++) {
        if (cached_packages[i] && strncmp(first_part, cached_packages[i], strlen(cached_packages[i])) == 0) {
            return 0;
        }
    }
    return 1;
}

/* --- Enum for return values --- */
typedef enum
{
    DENIED_USER_DENYLIST = 1,
    DENIED_STATIC_DENYLIST = 2,
    DENIED_BUILTINS_DENYLIST = 3,
    DENIED_NOT_FOUND = 4,

    ALLOWED_USER_ALLOWLIST = 100,
    ALLOWED_STATIC_ALLOWLIST = 101,
    ALLOWED_FIRST_PARTY_ALLOWLIST = 102
} IastPatchResult;

/* --- Helper function to build a list from an environment variable --- */
static char**
get_list_from_env(const char* env_var_name, size_t* count)
{
    const char* env_value = getenv(env_var_name);
    static char** modules_list = NULL;
    size_t count_tmp = 0;
    if (env_value && env_value[0] != '\0') {
        char* env_copy = strdup(env_value);
        if (!env_copy)
            return NULL;
        char* token = strtok(env_copy, ",");
        while (token) {
            count_tmp++;
            token = strtok(NULL, ",");
        }
        free(env_copy);

        if (count_tmp > 0) {
            modules_list = malloc(count_tmp * sizeof(char*));
            if (!modules_list) {
                PyErr_SetString(PyExc_MemoryError, "Failed to allocate memory for user allowlist");
                return NULL;
            }
            env_copy = strdup(env_value);
            if (!env_copy)
                return NULL;
            count_tmp = 0;
            token = strtok(env_copy, ",");
            while (token) {
                char* dup = strdup(token);
                if (!dup) {
                    free(env_copy);
                    return NULL;
                }
                for (char* p = dup; *p; p++) {
                    *p = tolower(*p);
                }
                modules_list[count_tmp++] = dup;
                token = strtok(NULL, ",");
            }
            free(env_copy);
        }
    }
    *count = count_tmp;
    return modules_list;
}

/* --- Helper function to free a dynamically allocated list --- */
static void
free_list(char** list, size_t count)
{
    if (list != NULL) {
        for (size_t i = 0; i < count; i++) {
            if (list[i] != NULL) {
                free(list[i]);
            }
        }
        free(list);
    }
}

/* --- Function init_globals ---
   Initializes global lists:
   1. builtins_denylist is composed of static stdlib names and sys.builtin_module_names.
   This implementation uses C char arrays and avoids excessive creation of PyTuples and PyObjects.
*/
static int
init_globals(void)
{
    /* 1. Initialize builtins_denylist */
    /* Get sys.builtin_module_names (borrowed reference) */
    size_t i;
    size_t builtin_count;
    PyObject* builtin_names = PySys_GetObject("builtin_module_names");

    if (!builtin_names || !PyTuple_Check(builtin_names)) {
        PyErr_SetString(PyExc_RuntimeError, "Could not get builtin_module_names");
        return -1;
    }

    builtin_count = (size_t)PyTuple_Size(builtin_names);
    builtins_denylist_count = static_stdlib_count + builtin_count;

    builtins_denylist = malloc(builtins_denylist_count * sizeof(char*));
    if (!builtins_denylist) {
        PyErr_NoMemory();
        return -1;
    }
    /* Copy static stdlib names */
    for (i = 0; i < static_stdlib_count; i++) {
        char* dup = strdup(static_stdlib_denylist[i]);
        if (!dup)
            return -1;
        for (char* p = dup; *p; p++) {
            *p = tolower(*p);
        }
        builtins_denylist[i] = dup;
    }

    /* Copy built-in module names */
    for (size_t i = 0; i < builtin_count; i++) {
        PyObject* item = PyTuple_GetItem(builtin_names, i); /* borrowed reference */
        if (PyUnicode_Check(item)) {
            const char* s = PyUnicode_AsUTF8(item);
            if (s) {
                char* dup = strdup(s);
                if (!dup)
                    return -1;
                for (char* p = dup; *p; p++) {
                    *p = tolower(*p);
                }
                builtins_denylist[static_stdlib_count + i] = dup;
            }
        }
    }

    return 0; // Success
}

/* --- Exported Function: py_should_iast_patch ---
   Receives the module name (as a string) and determines, using char arrays,
   whether it should be patched. The logic minimizes the creation of PyTuples
   and uses mostly pure C comparisons.
*/
static PyObject*
py_should_iast_patch(PyObject* self, PyObject* args)
{
    const char* module_name;

    if (!PyArg_ParseTuple(args, "s", &module_name)) {
        return NULL;
    }

    if (strlen(module_name) == 0) {
        PyErr_SetString(PyExc_ValueError, "Invalid module name");
        return NULL;
    }

    if (strlen(module_name) > 512) {
        PyErr_SetString(PyExc_ValueError, "Module name too long");
        return NULL;
    }

    for (const char* p = module_name; *p; p++) {
        if (!isalnum(*p) && *p != '.' && *p != '_') {
            PyErr_SetString(PyExc_ValueError, "Invalid characters in module name");
            return NULL;
        }
    }

    /* Create lower_module: lowercase version of module_name with a trailing '.' */
    char lower_module[512];
    strncpy(lower_module, module_name, sizeof(lower_module) - 1);
    lower_module[sizeof(lower_module) - 1] = '\0';
    for (size_t i = 0; i < strlen(lower_module); i++) {
        lower_module[i] = tolower(lower_module[i]);
    }
    size_t l = strlen(lower_module);
    if (l < sizeof(lower_module) - 1) {
        lower_module[l] = '.';
        lower_module[l + 1] = '\0';
    }
    /* Check in the user_allowlist */
    if (user_allowlist_count > 0 && str_in_list(lower_module, (const char**)user_allowlist, user_allowlist_count)) {
        return PyLong_FromLong(ALLOWED_USER_ALLOWLIST);
    }

    /* Check in the user_denylist */
    if (user_denylist_count > 0 && str_in_list(lower_module, (const char**)user_denylist, user_denylist_count)) {
        return PyLong_FromLong(DENIED_USER_DENYLIST);
    }

    /* Extract the first component (before the dot) and convert it to lowercase */
    char first_part[256];
    get_first_part_lower(module_name, first_part, sizeof(first_part));

    /* If the first component is in the not-patchable list, return False */
    if (str_in_list(first_part, (const char**)builtins_denylist, builtins_denylist_count)) {
        return PyLong_FromLong(DENIED_BUILTINS_DENYLIST);
    }

    /* Allow if it's a first-party module */
    if (is_first_party(module_name)) {
        return PyLong_FromLong(ALLOWED_FIRST_PARTY_ALLOWLIST);
    }

    /* Check in the static allow/deny lists */
    if (str_in_list(lower_module, static_allowlist, static_allowlist_count)) {
        if (str_in_list(lower_module, static_denylist, static_denylist_count)) {
            return PyLong_FromLong(DENIED_STATIC_DENYLIST);
        }
        return PyLong_FromLong(ALLOWED_STATIC_ALLOWLIST);
    }
    return PyLong_FromLong(DENIED_NOT_FOUND);
}

int
build_list_from_env(const char* env_var_name)
{
    size_t count = 0;
    char** result_list = get_list_from_env(env_var_name, &count);
    if (result_list == NULL) {
        return 0;
    }
    char** old_list = NULL;
    size_t old_count = 0;

    if (strcmp(env_var_name, "_DD_IAST_PATCH_MODULES") == 0) {
        old_list = user_allowlist;
        old_count = user_allowlist_count;
        user_allowlist = result_list;
        user_allowlist_count = count;
    } else if (strcmp(env_var_name, "_DD_IAST_DENY_MODULES") == 0) {
        old_list = user_denylist;
        old_count = user_denylist_count;
        user_denylist = result_list;
        user_denylist_count = count;
    } else {
        free_list(result_list, count);
        return -1;
    }

    return 0;
}
/* --- Exported function to build a list from an environment variable and update globals --- */
static PyObject*
py_build_list_from_env(PyObject* self, PyObject* args)
{
    const char* env_var_name;
    if (!PyArg_ParseTuple(args, "s", &env_var_name)) {
        return NULL;
    }
    int result = build_list_from_env(env_var_name);
    if (result >= 0) {
        Py_RETURN_TRUE;
    }
    Py_RETURN_FALSE;
}

/* --- Exported Function:  to return the user_allowlist as a Python list --- */
static PyObject*
py_get_user_allowlist(PyObject* self, PyObject* args)
{
    /* Convert the C list (user_allowlist) to a Python list */
    PyObject* py_list = PyList_New(user_allowlist_count);
    if (py_list == NULL) {
        return NULL;
    }

    for (size_t i = 0; i < user_allowlist_count; i++) {
        PyObject* py_str = PyUnicode_FromString(user_allowlist[i]);
        if (py_str == NULL) {
            Py_DECREF(py_list);
            return NULL;
        }
        PyList_SetItem(py_list, i, py_str);
    }

    return py_list;
}

static PyMethodDef IastPatchMethods[] = {
    { "build_list_from_env",
      py_build_list_from_env,
      METH_VARARGS,
      "Builds a Python list from a comma-separated environment variable and updates global lists." },
    { "should_iast_patch",
      py_should_iast_patch,
      METH_VARARGS,
      "Checks if a module should be patched based on allowlist and denylist." },
    { "get_user_allowlist",
      py_get_user_allowlist,
      METH_NOARGS,
      "Returns the current user allowlist as a Python list." },
    { NULL, NULL, 0, NULL }
};

static struct PyModuleDef iastpatchmodule = { PyModuleDef_HEAD_INIT,
                                              "iastpatch", /* Module name */
                                              "Module to decide if a module should be patched for IAST", /* Docstring */
                                              -1,
                                              IastPatchMethods };

PyMODINIT_FUNC
PyInit_iastpatch(void)
{
    if (init_globals() == -1)
        return NULL;

    PyObject* m = PyModule_Create(&iastpatchmodule);
    if (m == NULL)
        return NULL;

    /* --- Exporting enum values to Python --- */
    PyModule_AddIntConstant(m, "DENIED_USER_DENYLIST", DENIED_USER_DENYLIST);
    PyModule_AddIntConstant(m, "DENIED_STATIC_DENYLIST", DENIED_STATIC_DENYLIST);
    PyModule_AddIntConstant(m, "DENIED_BUILTINS_DENYLIST", DENIED_BUILTINS_DENYLIST);
    PyModule_AddIntConstant(m, "DENIED_NOT_FOUND", DENIED_NOT_FOUND);

    PyModule_AddIntConstant(m, "ALLOWED_USER_ALLOWLIST", ALLOWED_USER_ALLOWLIST);
    PyModule_AddIntConstant(m, "ALLOWED_STATIC_ALLOWLIST", ALLOWED_STATIC_ALLOWLIST);
    PyModule_AddIntConstant(m, "ALLOWED_FIRST_PARTY_ALLOWLIST", ALLOWED_FIRST_PARTY_ALLOWLIST);

    return m;
}

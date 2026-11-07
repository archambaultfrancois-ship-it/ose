/* Relocatable CPython entry point for Linux and Android release bundles.
 * This executable links only to the platform loader, never to system Python.
 */
#define _GNU_SOURCE
#include <Python.h>
#include <dlfcn.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#ifndef OSE_PYTHON_LIBRARY
#error OSE_PYTHON_LIBRARY must name the bundled libpython
#endif

static void fail(const char *message) {
    fprintf(stderr, "OSE private Python: %s\n", message);
    exit(1);
}

#define LOAD(symbol) \
    __typeof__(&symbol) call_##symbol = (__typeof__(&symbol))dlsym(library, #symbol); \
    if (!call_##symbol) fail(dlerror())

int main(int argc, char **argv) {
    char executable[PATH_MAX], home[PATH_MAX], libraries[PATH_MAX], libpython[PATH_MAX];
    ssize_t length = readlink("/proc/self/exe", executable, sizeof(executable) - 1);
    if (length < 0 || length >= (ssize_t)sizeof(executable) - 1)
        fail("cannot locate the running executable");
    executable[length] = '\0';
    if (snprintf(home, sizeof(home), "%s", executable) >= (int)sizeof(home))
        fail("executable path too long");
    char *separator = strrchr(home, '/');
    if (!separator) fail("invalid executable path");
    *separator = '\0';
    separator = strrchr(home, '/');
    if (!separator) fail("invalid runtime path");
    *separator = '\0';
    if (snprintf(libraries, sizeof(libraries), "%s/lib", home) >= (int)sizeof(libraries) ||
        snprintf(libpython, sizeof(libpython), "%s/lib/%s", home, OSE_PYTHON_LIBRARY) >= (int)sizeof(libpython))
        fail("runtime path too long");

    /* The ELF loader reads its search path at process startup. Re-exec once so
     * transitive libraries and subsequently loaded extension modules use ours.
     * This also works when Python starts a child through sys.executable.
     */
    const char *inherited = getenv("LD_LIBRARY_PATH");
    size_t size = strlen(libraries);
    if (!inherited || strncmp(inherited, libraries, size) ||
        (inherited[size] != '\0' && inherited[size] != ':')) {
        char *search;
        if (asprintf(&search, "%s%s%s", libraries, inherited && *inherited ? ":" : "",
                     inherited ? inherited : "") < 0)
            fail("cannot allocate library search path");
        if (setenv("LD_LIBRARY_PATH", search, 1)) fail("cannot set library search path");
        free(search);
        execv(executable, argv);
        fail("cannot restart with private libraries");
    }

    void *library = dlopen(libpython, RTLD_NOW | RTLD_GLOBAL);
    if (!library) fail(dlerror());
    LOAD(PyConfig_InitPythonConfig);
    LOAD(PyConfig_SetBytesString);
    LOAD(PyConfig_SetBytesArgv);
    LOAD(PyConfig_Clear);
    LOAD(Py_InitializeFromConfig);
    LOAD(PyStatus_Exception);
    LOAD(Py_ExitStatusException);
    LOAD(Py_RunMain);

    PyConfig config;
    call_PyConfig_InitPythonConfig(&config);
    config.user_site_directory = 0;
    config.write_bytecode = 0;
    PyStatus status = call_PyConfig_SetBytesString(&config, &config.home, home);
    if (call_PyStatus_Exception(status)) goto done;
    status = call_PyConfig_SetBytesString(&config, &config.program_name, executable);
    if (call_PyStatus_Exception(status)) goto done;
    status = call_PyConfig_SetBytesString(&config, &config.executable, executable);
    if (call_PyStatus_Exception(status)) goto done;
    status = call_PyConfig_SetBytesString(&config, &config.platlibdir, "lib");
    if (call_PyStatus_Exception(status)) goto done;
    status = call_PyConfig_SetBytesArgv(&config, argc, argv);
    if (call_PyStatus_Exception(status)) goto done;
    status = call_Py_InitializeFromConfig(&config);
done:
    call_PyConfig_Clear(&config);
    if (call_PyStatus_Exception(status)) call_Py_ExitStatusException(status);
    return call_Py_RunMain();
}

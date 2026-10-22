// Save coverage before the simulator's original fatal handler terminates.
// Loaded only into isolated RTL assertion-test processes on Linux.
#include <cstdio>
#include <cstdlib>
#include <dlfcn.h>
#include <string>

namespace {
using CovContext = void* (*)();
using CovWrite = void (*)(void*, const std::string&);
using Fatal = void (*)(const char*, int, const char*, const char*);
CovContext context = nullptr;
CovWrite write_coverage = nullptr;
Fatal original_fatal = nullptr;
std::string coverage_path;
bool saving = false;
}

extern "C" int toffee_configure_fatal_coverage(const char* model, const char* output) {
    // Use the already loaded DUT's own coverage implementation and ABI;
    // linking another Verilator runtime would create a separate counter set.
    void* handle = dlopen(model, RTLD_NOW | RTLD_NOLOAD);
    if (!handle) return 1;
    context = reinterpret_cast<CovContext>(dlsym(handle, "_ZN12VerilatedCov10threadCovpEv"));
    write_coverage = reinterpret_cast<CovWrite>(dlsym(handle,
        "_ZN19VerilatedCovContext5writeERKNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEE"));
    original_fatal = reinterpret_cast<Fatal>(dlsym(handle, "_Z8vl_fatalPKciS0_S0_"));
    if (!context || !write_coverage || !original_fatal) return 2;
    coverage_path = output;
    return 0;
}

void vl_fatal(const char* filename, int line, const char* hierarchy, const char* message) {
    if (original_fatal && !saving) {
        saving = true;
        write_coverage(context(), coverage_path);
        std::fprintf(stderr, "toffee: RTL coverage saved before fatal\n");
        std::fflush(stderr);
    }
    if (original_fatal) original_fatal(filename, line, hierarchy, message);
    // Preserve fatal behavior even if the library ABI is unsupported. The
    // Python setup rejects that ABI before running any stimulus.
    std::abort();
}

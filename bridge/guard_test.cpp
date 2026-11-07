// Proves that two plug-in instances cannot both use the bridge. Uses its own shared
// memory, so it is safe to run while the real bridge is playing.
#include "bridge_shm.h"
#include <cstdio>
#include <dlfcn.h>
#include <vector>

typedef void* Handle;
struct Descriptor {
    const char* uri;
    Handle (*instantiate)(const Descriptor*, double, const char*, const void* const*);
    void (*connect_port)(Handle, uint32_t, void*);
    void (*activate)(Handle);
    void (*run)(Handle, uint32_t);
    void (*deactivate)(Handle);
    void (*cleanup)(Handle);
    const void* (*extension_data)(const char*);
};
constexpr uint32_t N = 256;
struct Instance {
    Handle h; float l[N], r[N], sl[N], sr[N], gain = 1;
    void attach(const Descriptor* d) {
        h = d->instantiate(d, 48000, "", nullptr);
        d->connect_port(h, 0, l); d->connect_port(h, 1, r); d->connect_port(h, 2, sl); d->connect_port(h, 3, sr); d->connect_port(h, 5, &gain);
        std::fill_n(sl, N, 0.f); std::fill_n(sr, N, 0.f);
    }
    float peak() const { float m = 0; for (float v : l) m = std::max(m, std::fabs(v)); return m; }
};
static int failures = 0;
static void check(const char* what, bool ok) { printf("%s %s\n", ok ? "PASS" : "FAIL", what); if (!ok) ++failures; }
static double phase = 0;
static void feed(nb::Shared* s, uint32_t blocks) {  // stands in for norns
    float l[N];
    for (uint32_t b = 0; b < blocks; ++b) {
        for (auto& v : l) { v = 0.5f * std::sin(phase); phase += 0.05; }
        s->to_machinista.push(l, l, N);
    }
}
static void wait_ms(long ms) { timespec t{ms / 1000, (ms % 1000) * 1000000}; nanosleep(&t, nullptr); }

int main() {
    shm_unlink(nb::kShmName);
    auto* s = nb::bridge_open();
    void* lib = dlopen("./guard_test_plugin.so", RTLD_NOW);
    if (!s || !lib) { puts(dlerror()); return 2; }
    auto* get = reinterpret_cast<const Descriptor* (*)(uint32_t)>(dlsym(lib, "lv2_descriptor"));
    const Descriptor* d = get(0);
    static Instance a, b;
    a.attach(d); b.attach(d);

    float peak_a = 0, peak_b = 0;
    const auto both = [&](int blocks) {
        peak_a = peak_b = 0;
        for (int i = 0; i < blocks; ++i) {
            feed(s, 1);
            d->run(a.h, N); peak_a = std::max(peak_a, a.peak());
            d->run(b.h, N); peak_b = std::max(peak_b, b.peak());
            wait_ms(5);
        }
    };
    feed(s, 4);
    uint64_t written = s->to_norns.write.load();
    both(100);
    check("first instance plays", peak_a > 0.3f);
    check("second instance stays silent", peak_b == 0.f);
    check("only one writer into norns", s->to_norns.write.load() - written == 100 * N);
    check("refused blocks are counted", s->refused_blocks.load() == 100);

    // The first instance stops being run: after the timeout the other one takes over.
    wait_ms(250);
    peak_b = 0;
    for (int i = 0; i < 100; ++i) { feed(s, 1); d->run(b.h, N); peak_b = std::max(peak_b, b.peak()); wait_ms(5); }
    check("second instance takes over after the first stops", peak_b > 0.3f);

    // Removing the owner frees the bridge at once.
    d->cleanup(b.h);
    check("removing the owner frees the bridge", s->owner.load() == 0);
    d->run(a.h, N);
    check("remaining instance can claim it immediately", s->owner.load() != 0);
    d->cleanup(a.h);
    shm_unlink(nb::kShmName);
    puts(failures ? "GUARD_TEST_FAILED" : "GUARD_TEST_PASSED");
    return failures ? 1 : 0;
}

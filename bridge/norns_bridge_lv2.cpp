// "Norns Bridge" LV2 instrument for Machinista.
// Outputs norns' audio; its sidechain input is sent to norns' input.
#include "bridge_shm.h"
#include <cstdlib>

extern "C" {
typedef void* LV2_Handle;
typedef struct { const char* URI; void* data; } LV2_Feature;
typedef struct LV2_Descriptor {
    const char* URI;
    LV2_Handle (*instantiate)(const LV2_Descriptor*, double, const char*, const LV2_Feature* const*);
    void (*connect_port)(LV2_Handle, uint32_t, void*);
    void (*activate)(LV2_Handle);
    void (*run)(LV2_Handle, uint32_t);
    void (*deactivate)(LV2_Handle);
    void (*cleanup)(LV2_Handle);
    const void* (*extension_data)(const char*);
} LV2_Descriptor;
}

namespace {
enum Port { OUT_L, OUT_R, SIDE_L, SIDE_R, MIDI_IN, GAIN };
struct Plugin {
    nb::Shared* shared = nullptr;
    nb::AdaptiveReader reader;
    double rate = 48000;
    float* out_l = nullptr; float* out_r = nullptr;
    const float* side_l = nullptr; const float* side_r = nullptr;
    const float* gain = nullptr;
    float zero[8192] = {};
};
LV2_Handle instantiate(const LV2_Descriptor*, double rate, const char*, const LV2_Feature* const*) {
    auto* p = new Plugin;
    p->rate = rate;
    p->shared = nb::bridge_open();
    return p;
}
void connect_port(LV2_Handle h, uint32_t port, void* data) {
    auto* p = static_cast<Plugin*>(h);
    switch (port) {
        case OUT_L: p->out_l = static_cast<float*>(data); break;
        case OUT_R: p->out_r = static_cast<float*>(data); break;
        case SIDE_L: p->side_l = static_cast<const float*>(data); break;
        case SIDE_R: p->side_r = static_cast<const float*>(data); break;
        case GAIN: p->gain = static_cast<const float*>(data); break;
        default: break;
    }
}
void run(LV2_Handle h, uint32_t n) {
    auto* p = static_cast<Plugin*>(h);
    if (!p->shared || !nb::claim_bridge(*p->shared, reinterpret_cast<uintptr_t>(p), nb::now_ns())) {
        std::fill_n(p->out_l, n, 0.f);
        std::fill_n(p->out_r, n, 0.f);
        if (p->shared) p->shared->refused_blocks.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    const float* sl = p->side_l && n <= 8192 ? p->side_l : p->zero;
    const float* sr = p->side_r && n <= 8192 ? p->side_r : p->zero;
    p->shared->to_norns.push(sl, sr, n);
    const int configured = p->shared->target_frames.load(std::memory_order_relaxed);
    p->reader.read(p->shared->to_machinista, p->shared->out_stats, p->out_l, p->out_r, n,
                   configured > 0 ? configured : nb::kDefaultTarget, n / p->rate);
    const float g = p->gain ? std::clamp(*p->gain, 0.f, 2.f) : 1.f;
    for (uint32_t i = 0; i < n; ++i) { p->out_l[i] *= g; p->out_r[i] *= g; }
    p->shared->plugin_blocks.fetch_add(1, std::memory_order_relaxed);
}
void cleanup(LV2_Handle h) {
    auto* p = static_cast<Plugin*>(h);
    uint64_t self = reinterpret_cast<uintptr_t>(p);
    if (p->shared) p->shared->owner.compare_exchange_strong(self, 0);
    if (p->shared) munmap(p->shared, sizeof(nb::Shared));
    delete p;
}
const void* extension_data(const char*) { return nullptr; }
void noop(LV2_Handle) {}
const LV2_Descriptor descriptor = {"https://github.com/snassen/machinista-norns#bridge", instantiate, connect_port, noop, run, noop, cleanup, extension_data};
}  // namespace

extern "C" __attribute__((visibility("default"))) const LV2_Descriptor* lv2_descriptor(uint32_t index) {
    return index == 0 ? &descriptor : nullptr;
}

// Shared memory between Machinista's LV2 plug-in and the JACK bridge daemon.
// Lock-free and allocation-free: every function below is safe on an audio thread,
// except bridge_open(), which must only run from a non-realtime context.
#pragma once
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdint>
#include <ctime>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

namespace nb {
#ifndef NB_SHM_NAME
#define NB_SHM_NAME "/norns-bridge-v2"
#endif
constexpr const char* kShmName = NB_SHM_NAME;
constexpr uint64_t kCapacity = 1u << 14;  // frames, power of two
constexpr uint64_t kMask = kCapacity - 1;

inline int64_t now_ns() {  // vDSO call, safe on an audio thread
    timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return int64_t(t.tv_sec) * 1000000000 + t.tv_nsec;
}

struct Ring {  // stereo, one writer and one reader, positions only grow
    std::atomic<uint64_t> write{0};
    std::atomic<int64_t> write_ns{0};
    float data[kCapacity * 2];
    void push(const float* l, const float* r, uint32_t n) {
        uint64_t w = write.load(std::memory_order_relaxed);
        for (uint32_t i = 0; i < n; ++i, ++w) {
            data[(w & kMask) * 2] = l[i];
            data[(w & kMask) * 2 + 1] = r[i];
        }
        write_ns.store(now_ns(), std::memory_order_relaxed);
        write.store(w, std::memory_order_release);
    }
};

struct DirectionStats {
    std::atomic<uint64_t> underruns{0}, resyncs{0}, locks{0};
    std::atomic<int64_t> fill{0};
    std::atomic<float> ppm{0};
};

struct Shared {
    Ring to_machinista, to_norns;
    DirectionStats out_stats, in_stats;  // out = norns to Machinista
    std::atomic<uint64_t> plugin_blocks{0}, daemon_blocks{0};
    std::atomic<int32_t> target_frames{0};  // 0 = default
    // New fields go last so a running plug-in or daemon built earlier keeps working.
    std::atomic<uint64_t> owner{0};            // the one plug-in instance using the bridge
    std::atomic<int64_t> owner_ns{0};          // when that instance last ran
    std::atomic<uint64_t> refused_blocks{0};   // blocks a second instance stayed silent for
};

constexpr int64_t kOwnerTimeoutNs = 200000000;  // an instance silent this long gives up the bridge

// Only one plug-in instance may use the rings. Safe on an audio thread.
inline bool claim_bridge(Shared& s, uint64_t id, int64_t now) {
    uint64_t current = s.owner.load(std::memory_order_acquire);
    if (current != id) {
        if (current != 0 && now - s.owner_ns.load(std::memory_order_relaxed) < kOwnerTimeoutNs) return false;
        if (!s.owner.compare_exchange_strong(current, id, std::memory_order_acq_rel)) return false;
    }
    s.owner_ns.store(now, std::memory_order_relaxed);
    return true;
}

inline Shared* bridge_open() {
    const int fd = shm_open(kShmName, O_CREAT | O_RDWR, 0660);
    if (fd < 0) return nullptr;
    // Only ever grow: shrinking would wipe fields used by a newer build still running.
    struct stat info;
    if (fstat(fd, &info) != 0 || (size_t(info.st_size) < sizeof(Shared) && ftruncate(fd, sizeof(Shared)) != 0)) {
        close(fd);
        return nullptr;
    }
    void* p = mmap(nullptr, sizeof(Shared), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    return p == MAP_FAILED ? nullptr : static_cast<Shared*>(p);
}

// Reads a ring written on another clock. It keeps the fill level at a target by
// resampling very slightly, which absorbs clock drift without clicks.
class AdaptiveReader {
    static constexpr double kKp = 6.5e-6, kKi = 3.0e-7, kLimit = 1.0e-3;
    bool locked_ = false;
    double pos_ = 0, smooth_ = 0, integral_ = 0, ratio_ = 1;
    float gain_ = 0;  // short fades hide resynchronisation

    static float hermite(float a, float b, float c, float d, float t) {
        const float c1 = 0.5f * (c - a), c2 = a - 2.5f * b + 2.f * c - 0.5f * d, c3 = 0.5f * (d - a) + 1.5f * (b - c);
        return ((c3 * t + c2) * t + c1) * t + b;
    }

public:
    void read(const Ring& ring, DirectionStats& stats, float* l, float* r, uint32_t n, int target, double block_seconds) {
        const uint64_t w = ring.write.load(std::memory_order_acquire);
        const double need = n * (1 + kLimit) + 4;
        if (locked_ && (double(w) - pos_ < need)) {
            locked_ = false;
            stats.underruns.fetch_add(1, std::memory_order_relaxed);
        }
        if (locked_ && (double(w) - pos_ > double(kCapacity) - 2048)) {
            pos_ = double(w) - target;
            smooth_ = target;
            stats.resyncs.fetch_add(1, std::memory_order_relaxed);
        }
        if (!locked_) {
            std::fill_n(l, n, 0.f);
            std::fill_n(r, n, 0.f);
            gain_ = 0;
            if (w >= uint64_t(target) + n + 4) {
                pos_ = double(w) - target;
                smooth_ = target;
                locked_ = true;
                stats.locks.fetch_add(1, std::memory_order_relaxed);
            }
            stats.fill.store(int64_t(w) - int64_t(pos_), std::memory_order_relaxed);
            return;
        }
        // The writer's blocks arrive at a slowly moving phase relative to ours. Project
        // its position to "now" so that this phase does not look like a level change.
        const uint64_t w2 = ring.write.load(std::memory_order_acquire);
        const double rate_hz = n / block_seconds;
        const double elapsed = double(now_ns() - ring.write_ns.load(std::memory_order_relaxed)) * 1e-9;
        const double projected = double(w2) + std::clamp(elapsed, 0.0, 0.05) * rate_hz;
        const double fill = projected - pos_;
        smooth_ += (fill - smooth_) * std::min(1.0, block_seconds / 0.5);
        const double error = smooth_ - target;
        integral_ = std::clamp(integral_ + error * kKi * block_seconds, -kLimit, kLimit);
        ratio_ = 1.0 + std::clamp(error * kKp + integral_, -kLimit, kLimit);
        const float step = 1.f / std::max<uint32_t>(n, 1) * 4.f;
        for (uint32_t i = 0; i < n; ++i) {
            const uint64_t k = uint64_t(pos_);
            const float t = float(pos_ - double(k));
            const auto at = [&](uint64_t f, int c) { return ring.data[(f & kMask) * 2 + c]; };
            gain_ = std::min(1.f, gain_ + step);
            l[i] = gain_ * hermite(at(k - 1, 0), at(k, 0), at(k + 1, 0), at(k + 2, 0), t);
            r[i] = gain_ * hermite(at(k - 1, 1), at(k, 1), at(k + 1, 1), at(k + 2, 1), t);
            pos_ += ratio_;
        }
        stats.fill.store(int64_t(fill), std::memory_order_relaxed);
        stats.ppm.store(float((ratio_ - 1.0) * 1e6), std::memory_order_relaxed);
    }
};
constexpr int kDefaultTarget = 768;  // frames of cushion, about 16 ms at 48 kHz
// Machinista writes the return path from its audio thread, whose wake-ups were measured
// up to 15.4 ms apart with the MK3. That is 739 frames, so this path needs a deeper cushion.
constexpr int kInputTarget = 1344;  // about 28 ms
}  // namespace nb

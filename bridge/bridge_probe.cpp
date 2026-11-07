// Watches both ring writers from outside and reports stalls and bursts.
// Usage: bridge_probe SECONDS
#include "bridge_shm.h"
#include <cstdio>
#include <cstdlib>
#include <pthread.h>
#include <sched.h>

struct Watch {
    const char* name; uint64_t last; int64_t last_ns; int64_t max_gap = 0; uint64_t events = 0;
    uint64_t window_start; int64_t window_ns;
};

int main(int argc, char** argv) {
    const int secs = argc > 1 ? atoi(argv[1]) : 30;
    sched_param sp{}; sp.sched_priority = 5;
    if (pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp) != 0) puts("warning: probe not realtime");
    auto* s = nb::bridge_open();
    if (!s) return 1;
    const int64_t t0 = nb::now_ns();
    Watch w[2] = {{"machinista>norns (plug-in writes)", s->to_norns.write.load(), t0},
                  {"norns>machinista (daemon writes)", s->to_machinista.write.load(), t0}};
    const std::atomic<uint64_t>* pos[2] = {&s->to_norns.write, &s->to_machinista.write};
    timespec nap{0, 500000};
    while (nb::now_ns() - t0 < int64_t(secs) * 1000000000) {
        nanosleep(&nap, nullptr);
        const int64_t now = nb::now_ns();
        for (int i = 0; i < 2; ++i) {
            const uint64_t p = pos[i]->load();
            if (p == w[i].last) continue;
            const int64_t gap = now - w[i].last_ns;
            w[i].max_gap = std::max(w[i].max_gap, gap);
            if (gap > 8000000) {
                ++w[i].events;
                printf("t=%7.3f %s gap=%.1fms wrote=%llu frames\n", (now - t0) * 1e-9, w[i].name, gap * 1e-6,
                       (unsigned long long)(p - w[i].last));
                fflush(stdout);
            }
            w[i].last = p; w[i].last_ns = now;
        }
    }
    for (auto& x : w) printf("%s: max_gap=%.2fms events=%llu\n", x.name, x.max_gap * 1e-6, (unsigned long long)x.events);
    return 0;
}

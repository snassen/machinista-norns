// Drives the real plug-in binary on a deliberately offset clock and checks both
// directions with sine waves. Exit code is 0 only if nothing clicked or dropped.
#include "bridge_shm.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <dlfcn.h>
#include <jack/jack.h>
#include <pthread.h>
#include <thread>
extern "C" { struct D { const char* URI; void* (*inst)(const D*, double, const char*, const void* const*); void (*conn)(void*, uint32_t, void*); void (*act)(void*); void (*run)(void*, uint32_t); }; }
struct Check {  // a pure sine satisfies x[n] = 2cos(w)x[n-1] - x[n-2]
    double c, x1 = 0, x2 = 0, worst = 0; uint64_t clicks = 0, seen = 0, skip = 48000; double peak = 0;
    explicit Check(double hz) : c(2 * std::cos(2 * M_PI * hz / 48000)) {}
    void feed(const float* b, uint32_t n) { for (uint32_t i = 0; i < n; ++i) { const double e = std::fabs(b[i] - c * x1 + x2); x2 = x1; x1 = b[i]; if (seen++ > skip) { peak = std::max(peak, std::fabs(double(b[i]))); worst = std::max(worst, e); if (e > 0.01) ++clicks; } } }
};
static jack_port_t* jout[2]; static jack_port_t* jin[2]; static double jphase = 0; static Check jcheck(1000);
static int jprocess(jack_nframes_t n, void*) {
    for (int c = 0; c < 2; ++c) { auto* o = static_cast<float*>(jack_port_get_buffer(jout[c], n)); double ph = jphase; for (jack_nframes_t i = 0; i < n; ++i) { o[i] = float(0.5 * std::sin(ph)); ph += 2 * M_PI * 440 / 48000; } if (c == 1) jphase = std::fmod(ph, 2 * M_PI); }
    jcheck.feed(static_cast<float*>(jack_port_get_buffer(jin[0], n)), n);
    return 0;
}
int main(int argc, char** argv) {
    const double ppm = atof(argv[2]); const int secs = atoi(argv[3]);
    void* lib = dlopen(argv[1], RTLD_NOW); if (!lib) { puts(dlerror()); return 2; }
    auto get = reinterpret_cast<const D* (*)(uint32_t)>(dlsym(lib, "lv2_descriptor")); const D* d = get(0);
    void* h = d->inst(d, 48000, "", nullptr);
    float ol[256], orr[256], sl[256], sr[256], midi[64] = {}, gain = 1; d->conn(h, 0, ol); d->conn(h, 1, orr); d->conn(h, 2, sl); d->conn(h, 3, sr); d->conn(h, 4, midi); d->conn(h, 5, &gain);
    jack_client_t* j = jack_client_open("bridge-test", JackNoStartServer, nullptr); if (!j) { puts("no jack"); return 2; }
    jout[0] = jack_port_register(j, "o1", JACK_DEFAULT_AUDIO_TYPE, JackPortIsOutput, 0); jout[1] = jack_port_register(j, "o2", JACK_DEFAULT_AUDIO_TYPE, JackPortIsOutput, 0);
    jin[0] = jack_port_register(j, "i1", JACK_DEFAULT_AUDIO_TYPE, JackPortIsInput, 0); jin[1] = jack_port_register(j, "i2", JACK_DEFAULT_AUDIO_TYPE, JackPortIsInput, 0);
    jack_set_process_callback(j, jprocess, nullptr); jack_activate(j);
    jack_connect(j, "bridge-test:o1", "machinista-bridge:in_1"); jack_connect(j, "bridge-test:o2", "machinista-bridge:in_2"); jack_connect(j, "machinista-bridge:out_1", "bridge-test:i1"); jack_connect(j, "machinista-bridge:out_2", "bridge-test:i2");
    sched_param sp{}; sp.sched_priority = 18; const int rt = pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp); printf("harness_rt=%d offset_ppm=%.0f\n", rt == 0, ppm);
    nb::Shared* s = nb::bridge_open(); Check pcheck(440); timespec next; clock_gettime(CLOCK_MONOTONIC, &next);
    const double period_ns = 256.0 / (48000 * (1 + ppm * 1e-6)) * 1e9; double frac = 0, phase = 0; int64_t worst_late = 0; const uint64_t blocks = uint64_t(secs * 48000.0 / 256);
    double pmin=1e9,pmax=-1e9,imin=1e9,imax=-1e9,psum=0,isum=0; uint64_t pn=0;
    const auto base = [&] { return std::make_pair(s->out_stats.underruns.load() + s->out_stats.resyncs.load(), s->in_stats.underruns.load() + s->in_stats.resyncs.load()); }();
    for (uint64_t b = 0; b < blocks; ++b) {
        for (int i = 0; i < 256; ++i) { sl[i] = sr[i] = float(0.5 * std::sin(phase)); phase += 2 * M_PI * 1000 / 48000; } phase = std::fmod(phase, 2 * M_PI);
        d->run(h, 256); pcheck.feed(ol, 256);
        frac += period_ns; const auto whole = int64_t(frac); frac -= whole; next.tv_nsec += whole; while (next.tv_nsec >= 1000000000) { next.tv_nsec -= 1000000000; ++next.tv_sec; }
        clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, nullptr);
        timespec now; clock_gettime(CLOCK_MONOTONIC, &now); worst_late = std::max<int64_t>(worst_late, (now.tv_sec - next.tv_sec) * 1000000000 + now.tv_nsec - next.tv_nsec);
        if (b > blocks / 2) { const double a=s->out_stats.ppm.load(), c=s->in_stats.ppm.load(); pmin=std::min(pmin,a);pmax=std::max(pmax,a);imin=std::min(imin,c);imax=std::max(imax,c);psum+=a;isum+=c;++pn; }
        if (b % (blocks / 6 + 1) == 0) printf("t=%3.0fs out fill=%ld ppm=%+.0f | in fill=%ld ppm=%+.0f\n", b * 256.0 / 48000, long(s->out_stats.fill.load()), s->out_stats.ppm.load(), long(s->in_stats.fill.load()), s->in_stats.ppm.load());
    }
    // Stop counting here: once the plug-in stops running, its signal ends on purpose.
    const uint64_t out_clicks = pcheck.clicks, in_clicks = jcheck.clicks;
    const auto u1 = s->out_stats.underruns.load() + s->out_stats.resyncs.load() - base.first, u2 = s->in_stats.underruns.load() + s->in_stats.resyncs.load() - base.second;
    printf("RESULT out(440Hz) clicks=%llu worst=%.5f peak=%.3f drops=%llu | in(1kHz) clicks=%llu worst=%.5f peak=%.3f drops=%llu | final ppm out=%+.0f in=%+.0f worst_wake=%.2fms\n", (unsigned long long)out_clicks, pcheck.worst, pcheck.peak, (unsigned long long)u1, (unsigned long long)in_clicks, jcheck.worst, jcheck.peak, (unsigned long long)u2, s->out_stats.ppm.load(), s->in_stats.ppm.load(), worst_late / 1e6);
    printf("PPM second half: out mean=%+.0f [%+.0f..%+.0f] | in mean=%+.0f [%+.0f..%+.0f]\n",psum/pn,pmin,pmax,isum/pn,imin,imax);
    fflush(stdout);
    jack_client_close(j);
    return (out_clicks || in_clicks || u1 || u2 || pcheck.peak < 0.4 || jcheck.peak < 0.4) ? 1 : 0;
}

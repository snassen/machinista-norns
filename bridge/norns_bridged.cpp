// JACK client on the norns side of the bridge. Sends crone's outputs to Machinista
// and Machinista's sidechain audio to crone's inputs.
#include "bridge_shm.h"
#include <csignal>
#include <cstdio>
#include <jack/jack.h>
#include <string_view>

static jack_port_t* in_l; static jack_port_t* in_r; static jack_port_t* out_l; static jack_port_t* out_r;
static nb::Shared* shared; static nb::AdaptiveReader reader; static double rate = 48000;
static volatile sig_atomic_t running = 1;

static int process(jack_nframes_t n, void*) {
    auto* il = static_cast<float*>(jack_port_get_buffer(in_l, n));
    auto* ir = static_cast<float*>(jack_port_get_buffer(in_r, n));
    auto* ol = static_cast<float*>(jack_port_get_buffer(out_l, n));
    auto* or_ = static_cast<float*>(jack_port_get_buffer(out_r, n));
    shared->to_machinista.push(il, ir, n);
    const int configured = shared->target_frames.load(std::memory_order_relaxed);
    reader.read(shared->to_norns, shared->in_stats, ol, or_, n, configured > 0 ? configured : nb::kInputTarget, n / rate);
    shared->daemon_blocks.fetch_add(1, std::memory_order_relaxed);
    return 0;
}

int main(int argc, char** argv) {
    const bool wire_crone = !(argc > 1 && std::string_view(argv[1]) == "--no-crone");
    shared = nb::bridge_open();
    if (!shared) { perror("shm"); return 1; }
    jack_client_t* client = jack_client_open("machinista-bridge", JackNoStartServer, nullptr);
    if (!client) { fputs("no JACK server\n", stderr); return 1; }
    rate = jack_get_sample_rate(client);
    in_l = jack_port_register(client, "in_1", JACK_DEFAULT_AUDIO_TYPE, JackPortIsInput, 0);
    in_r = jack_port_register(client, "in_2", JACK_DEFAULT_AUDIO_TYPE, JackPortIsInput, 0);
    out_l = jack_port_register(client, "out_1", JACK_DEFAULT_AUDIO_TYPE, JackPortIsOutput, 0);
    out_r = jack_port_register(client, "out_2", JACK_DEFAULT_AUDIO_TYPE, JackPortIsOutput, 0);
    jack_set_process_callback(client, process, nullptr);
    jack_activate(client);
    if (wire_crone) {
        bool wired = false;
        for (int i = 0; i < 300 && !wired; ++i) {
            const int a = jack_connect(client, "crone:output_1", "machinista-bridge:in_1");
            const int b = jack_connect(client, "crone:output_2", "machinista-bridge:in_2");
            const int c = jack_connect(client, "machinista-bridge:out_1", "crone:input_1");
            const int d = jack_connect(client, "machinista-bridge:out_2", "crone:input_2");
            const auto ok = [](int r) { return r == 0 || r == 17; };  // 17 = already connected
            wired = ok(a) && ok(b) && ok(c) && ok(d);
            if (!wired) usleep(200000);
        }
        puts(wired ? "BRIDGE_WIRED" : "BRIDGE_WIRE_FAILED");
        fflush(stdout);
    }
    std::signal(SIGINT, [](int) { running = 0; });
    std::signal(SIGTERM, [](int) { running = 0; });
    while (running) pause();
    jack_client_close(client);
    return 0;
}

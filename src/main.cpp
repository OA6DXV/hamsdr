#include "hamsdr/iq.hpp"
#include "hamsdr/rtl_tcp_client.hpp"
#include "hamsdr/spectrum.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>

namespace {
void usage(const char* program) {
    std::cerr << "Usage: " << program
              << " [--host HOST] [--port PORT] [--sample-rate HZ]"
                 " [--center-frequency HZ] [--seconds N] [--control]\n";
}
}

int main(int argc, char** argv) {
    hamsdr::RtlTcpConfig rtl;
    unsigned seconds = 10;
    for (int i = 1; i < argc; ++i) {
        const std::string argument = argv[i];
        auto value = [&]() -> std::string {
            if (++i >= argc) { usage(argv[0]); std::exit(2); }
            return argv[i];
        };
        if (argument == "--host") rtl.host = value();
        else if (argument == "--port") rtl.port = static_cast<std::uint16_t>(std::stoul(value()));
        else if (argument == "--sample-rate") rtl.sample_rate = static_cast<std::uint32_t>(std::stoul(value()));
        else if (argument == "--center-frequency") rtl.center_frequency = static_cast<std::uint32_t>(std::stoul(value()));
        else if (argument == "--seconds") seconds = static_cast<unsigned>(std::stoul(value()));
        else if (argument == "--control") rtl.control_device = true;
        else { usage(argv[0]); return 2; }
    }

    hamsdr::SpectrumEngine spectrum({4096, 4, -110.0F, -20.0F});
    std::mutex dsp_mutex;
    std::atomic<std::uint64_t> frames{0};
    hamsdr::RtlTcpClient client(
        rtl,
        [&](std::span<const std::uint8_t> bytes) {
            auto iq = hamsdr::convert_u8_iq(bytes);
            std::scoped_lock lock(dsp_mutex);
            for (const auto& frame : spectrum.push(iq)) {
                const auto peak = std::max_element(frame.begin(), frame.end());
                const auto bin = static_cast<std::size_t>(std::distance(frame.begin(), peak));
                const double offset = (static_cast<double>(bin) / static_cast<double>(frame.size()) - 0.5) *
                                      static_cast<double>(rtl.sample_rate);
                const auto number = ++frames;
                if (number % 10 == 0)
                    std::cout << "frame=" << number << " peak_db=" << *peak << " offset_hz=" << offset << '\n';
            }
        },
        [](std::string_view state) { std::cerr << "rtl_tcp=" << state << '\n'; });

    client.start();
    std::this_thread::sleep_for(std::chrono::seconds(seconds));
    client.stop();
    const auto metrics = client.metrics();
    std::cout << "connections=" << metrics.connections << " disconnects=" << metrics.disconnects
              << " bytes=" << metrics.bytes_received << " spectrum_frames=" << frames.load() << '\n';
    return metrics.bytes_received == 0 ? 1 : 0;
}

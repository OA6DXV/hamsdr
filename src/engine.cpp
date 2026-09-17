#include "hamsdr/iq.hpp"
#include "hamsdr/receiver.hpp"
#include "hamsdr/rtl_tcp_client.hpp"
#include "hamsdr/spectrum.hpp"
#include <atomic>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <sstream>
#include <csignal>
#include <numbers>

namespace {
std::mutex output_mutex;
void frame(unsigned type, unsigned id, std::span<const std::uint8_t> payload) {
    std::scoped_lock lock(output_mutex);
    std::uint8_t header[9]; header[0]=static_cast<std::uint8_t>(type);
    for (unsigned i=0;i<4;++i) {
        header[i+1]=static_cast<std::uint8_t>(id>>(8*i));
        header[i+5]=static_cast<std::uint8_t>(payload.size()>>(8*i));
    }
    std::cout.write(reinterpret_cast<char*>(header),9);
    std::cout.write(reinterpret_cast<const char*>(payload.data()),static_cast<std::streamsize>(payload.size()));
    std::cout.flush();
    if (!std::cout) std::_Exit(1);
}
void state(std::string_view text) {
    frame(4,0,{reinterpret_cast<const std::uint8_t*>(text.data()),text.size()});
}
}
int main(int argc,char** argv) {
    std::signal(SIGPIPE,SIG_IGN);
    if (argc!=7) { std::cerr<<"Usage: engine HOST PORT CENTER_HZ SAMPLE_RATE_HZ GAIN_MODE GAIN_TENTH_DB (or --demo 0 CENTER_HZ SAMPLE_RATE_HZ GAIN_MODE GAIN_TENTH_DB)\n"; return 2; }
    const bool demo=std::string(argv[1])=="--demo";
    std::uint32_t center_frequency=0,sample_rate=0;
    std::int32_t gain_tenth_db=0;
    const std::string gain_mode=argv[5];
    try {
        const auto center=std::stoull(argv[3]),rate=std::stoull(argv[4]);
        const auto gain=std::stoll(argv[6]);
        if (!center || center>UINT32_MAX || rate!=1'024'000 ||
            (gain_mode!="auto" && gain_mode!="manual") ||
            gain < INT32_MIN || gain > INT32_MAX) return 2;
        center_frequency=static_cast<std::uint32_t>(center);
        sample_rate=static_cast<std::uint32_t>(rate);
        gain_tenth_db=static_cast<std::int32_t>(gain);
    } catch (...) { return 2; }
    std::mutex mutex;
    std::map<unsigned,std::unique_ptr<hamsdr::Receiver>> receivers;
    // Each 65536-point FFT yields 15.625 shared rows/second. The gateway
    // selects per-client cadences from this stream without repeating rows.
    // At maximum 64x zoom the 16 kHz viewport still contains 1024 real bins.
    auto spectrum=std::make_unique<hamsdr::SpectrumEngine>(hamsdr::SpectrumConfig{65536,1,-120,0});
    auto consume=[&](std::span<const std::complex<float>> iq) {
        std::scoped_lock lock(mutex);
        if (receivers.empty()) return;
        for (const auto& row:spectrum->push(iq)) frame(1,0,spectrum->quantize(row));
        for (auto& [id,receiver]:receivers) {
            const auto audio=receiver->push(iq);
            std::vector<std::uint8_t> bytes; bytes.reserve(audio.size()*2);
            for (auto x:audio) { const auto u=static_cast<std::uint16_t>(x); bytes.push_back(u&255); bytes.push_back(u>>8); }
            if (!bytes.empty()) frame(2,id,bytes);
            const auto reading=std::to_string(receiver->power_db());
            frame(3,id,{reinterpret_cast<const std::uint8_t*>(reading.data()),reading.size()});
        }
    };
    std::unique_ptr<hamsdr::RtlTcpClient> source;
    std::jthread generator;
    if (demo) {
        generator=std::jthread([&](std::stop_token stop){
            state("demo");
            std::uint64_t n=0;
            auto deadline=std::chrono::steady_clock::now();
            while (!stop.stop_requested()) {
                std::vector<std::complex<float>> iq(16384);
                for (auto& x:iq) {
                    const double t=static_cast<double>(n++)/sample_rate;
                    auto tone=[&](double f){const double p=2*std::numbers::pi*f*t;return std::complex<float>(std::cos(p),std::sin(p));};
                    // Carrier reference is 7.1005 MHz. USB at 7100 kHz, LSB at
                    // 7090 kHz, AM at 7108 kHz. All carry a 1 kHz test tone.
                    x=0.15F*tone(500)+0.15F*tone(-11500)+
                      static_cast<float>(0.2*(1+0.5*std::cos(2*std::numbers::pi*1000*t)))*tone(7500);
                }
                consume(iq);
                deadline+=std::chrono::milliseconds(16);
                std::this_thread::sleep_until(deadline);
            }
        });
    } else {
        hamsdr::RtlTcpConfig config; config.host=argv[1];
        const auto port=std::stoul(argv[2]); if (!port || port>65535) return 2;
        config.port=static_cast<std::uint16_t>(port);
        config.center_frequency=center_frequency;
        config.sample_rate=sample_rate;
        config.control_device=true;
        config.manual_gain=gain_mode=="manual";
        config.gain_tenth_db=gain_tenth_db;
        source=std::make_unique<hamsdr::RtlTcpClient>(config,
            [&](auto bytes){consume(hamsdr::convert_u8_iq(bytes));},
            [&](auto event){
                if (event=="streaming") {
                    std::scoped_lock lock(mutex);
                    spectrum=std::make_unique<hamsdr::SpectrumEngine>(hamsdr::SpectrumConfig{65536,1,-120,0});
                }
                state(event);
            });
        source->start();
    }
    std::string line;
    while (std::getline(std::cin,line)) {
        if (line.size()>256) continue;
        std::istringstream parser(line); std::string command,mode; unsigned id;
        double offset; float low,high,squelch;
        if (!(parser>>command>>id) || id==0) continue;
        std::scoped_lock lock(mutex);
        if (command=="del") receivers.erase(id);
        else if (command=="set" && (parser>>offset>>mode>>low>>high>>squelch)) {
            unsigned notch=0,nr=0;
            parser>>notch>>nr;
            if (notch>1 || nr>4) continue;
            if (!receivers.contains(id) && receivers.size()>=20) continue;
            try {
                if (auto found=receivers.find(id);found!=receivers.end())
                    found->second->configure(offset,mode,low,high,squelch,notch!=0,nr);
                else receivers[id]=std::make_unique<hamsdr::Receiver>(offset,mode,low,high,squelch,notch!=0,nr);
            }
            catch (const std::exception& e) { std::cerr<<e.what()<<'\n'; }
        }
    }
    if (source) source->stop();
    if (generator.joinable()) {generator.request_stop();generator.join();}
}

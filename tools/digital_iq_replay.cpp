// SPDX-License-Identifier: GPL-3.0-only
// Offline replay only: never connects to rtl_tcp or sends receiver controls.
#include "hamsdr/iq.hpp"
#include "hamsdr/receiver.hpp"
#include <fstream>
#include <iostream>
#include <stdexcept>

int main(int argc,char** argv) {
    if (argc!=10) {
        std::cerr<<"Usage: digital-iq-replay IQ_FILE CENTER_HZ FREQUENCY_HZ MODE LOW HIGH SECONDS DIGITAL_0_OR_1 OUTPUT_PCM\n";
        return 2;
    }
    try {
        const double center=std::stod(argv[2]),frequency=std::stod(argv[3]),seconds=std::stod(argv[7]);
        const std::string mode=argv[4];
        const auto digital=std::stoi(argv[8]);
        if (!std::isfinite(seconds) || seconds<=0 || seconds>600 || (digital!=0 && digital!=1)) return 2;
        hamsdr::Receiver receiver(frequency-center-(mode=="CW"?700:0),mode,
            std::stof(argv[5]),std::stof(argv[6]),-150,false,0,digital!=0);
        std::ifstream input(argv[1],std::ios::binary);
        std::ofstream output(argv[9],std::ios::binary);
        if (!input || !output) throw std::runtime_error("cannot open replay input/output");
        std::vector<std::uint8_t> bytes(32768);
        auto remaining=static_cast<std::uint64_t>(seconds*1024000)*2;
        std::uint64_t count=0,rails=0;
        double power=0;unsigned peak=0;
        while (remaining) {
            const auto requested=std::min<std::uint64_t>(remaining,bytes.size());
            input.read(reinterpret_cast<char*>(bytes.data()),static_cast<std::streamsize>(requested));
            const auto length=static_cast<std::size_t>(input.gcount());
            if (!length) break;
            if (length%2) throw std::runtime_error("truncated complex IQ sample");
            remaining-=length;
            const auto audio=receiver.push(hamsdr::convert_u8_iq({bytes.data(),length}));
            for (const auto sample:audio) {
                const auto value=static_cast<std::uint16_t>(sample);
                output.put(static_cast<char>(value&255));output.put(static_cast<char>(value>>8));
                const auto magnitude=static_cast<unsigned>(std::abs(static_cast<int>(sample)));
                peak=std::max(peak,magnitude);rails+=magnitude>=31128;
                power+=static_cast<double>(sample)*sample;++count;
            }
        }
        if (!output || !count) throw std::runtime_error("empty or failed replay output");
        std::cout<<"rate="<<receiver.audio_rate()<<" samples="<<count<<" seconds="
                 <<static_cast<double>(count)/receiver.audio_rate()<<" peak="<<peak
                 <<" rms="<<std::sqrt(power/static_cast<double>(count))<<" clipped="<<rails<<'\n';
    } catch (const std::exception& error) { std::cerr<<error.what()<<'\n';return 1; }
}

// SPDX-License-Identifier: GPL-3.0-only
#include "hamsdr/digital_audio.hpp"
#include "hamsdr/receiver.hpp"
#include <complex>
#include <iostream>
#include <stdexcept>
#include <vector>

namespace {
void require(bool condition,const char* message) {
    if (!condition) throw std::runtime_error(message);
}
std::vector<float> resample(double frequency) {
    hamsdr::DigitalResampler converter;
    std::vector<float> result;
    for (unsigned n=0;n<32000;++n) {
        const auto sample=static_cast<float>(0.3*std::sin(2*std::numbers::pi*frequency*n/16000));
        float output;
        if (converter.push(sample,output)) result.push_back(output);
    }
    require(result.size()==24000,"incorrect 3:4 output sample count");
    return result;
}
double amplitude(const std::vector<float>& data,double frequency) {
    std::complex<double> total{};
    for (unsigned n=12000;n<data.size();++n) {
        const double phase=-2*std::numbers::pi*frequency*n/12000;
        total+=static_cast<double>(data[n])*std::complex<double>(std::cos(phase),std::sin(phase));
    }
    return 2*std::abs(total)/static_cast<double>(data.size()-12000);
}
}
int main() {
    for (double frequency:{700.0,1000.0,3000.0,5000.0}) {
        const double level=amplitude(resample(frequency),frequency);
        std::cout<<frequency<<" Hz passband gain "<<20*std::log10(level/0.3)<<" dB\n";
        require(std::abs(level/0.3-1)<0.005,"digital passband attenuation");
    }
    for (double frequency:{6100.0,6500.0,7000.0,7800.0}) {
        const double rejection=20*std::log10(std::max(1e-12,amplitude(resample(frequency),12000-frequency)/0.3));
        std::cout<<frequency<<" Hz alias level "<<rejection<<" dB\n";
        require(rejection < -65,"insufficient antialias rejection");
    }
    hamsdr::DigitalResampler continuous,chunked;
    std::vector<float> expected,actual;
    for (unsigned n=0;n<16003;++n) {
        float output;
        if (continuous.push(static_cast<float>(std::sin(n*0.17)),output)) expected.push_back(output);
    }
    for (unsigned base=0;base<16003;base+=127) {
        for (unsigned n=base;n<std::min(base+127,16003U);++n) {
            float output;
            if (chunked.push(static_cast<float>(std::sin(n*0.17)),output)) actual.push_back(output);
        }
    }
    require(actual==expected,"resampler changed across uneven chunk boundaries");
    chunked.reset();
    for (unsigned n=0;n<512;++n) {
        float output=1;
        if (chunked.push(0,output)) require(output==0,"reset retained stale audio");
    }
    hamsdr::DigitalGain gain;
    const float first=gain.push(0.2F);
    for (unsigned n=0;n<12000;++n) gain.push(0);
    require(std::abs(gain.push(0.02F)/first-0.1F)<1e-6F,"gain pumped during a 750 ms Morse pause");
    for (unsigned n=0;n<160000;++n) gain.push(0.001F);
    require(gain.push(0.02F)>first*0.15F,"gain did not recover after a long fade");
    require(std::abs(gain.push(10.0F))<=0.151F,"strong signal clipped digital normalization");
    gain.reset();require(std::abs(gain.push(0.01F)-0.075F)<1e-5F,"gain reset failed");

    hamsdr::Receiver receiver(0,"USB",0,5000,-150,false,0,true);
    std::vector<std::complex<float>> iq(16384,{0.01F,0});
    require(receiver.audio_rate()==12000 && receiver.push(iq).size()==192,"digital receiver rate");
    receiver.configure(0,"USB",0,5000,-150,false,0,false);
    require(receiver.audio_rate()==16000 && receiver.push(iq).size()==256,"listening rate restore");
    receiver.configure(0,"USB",0,5000,-150,true,4,true);
    require(receiver.push(iq).size()==192,"digital mode switch rate");
    std::cout<<"Digital FIR, gain-hold and rate-transition tests passed\n";
}

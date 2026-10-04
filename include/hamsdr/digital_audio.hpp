// SPDX-License-Identifier: GPL-3.0-only
#pragma once
#include <array>
#include <algorithm>
#include <cmath>
#include <numbers>

namespace hamsdr {
// Causal 3:4 polyphase FIR: 16 kHz float input, 12 kHz float output.
// The 5.5 kHz cutoff preserves the configured 0-5 kHz digital passband.
// 129 taps per phase add 4 ms delay without buffering complete audio blocks.
class DigitalResampler {
public:
    DigitalResampler() {
        constexpr double width = 11000.0 / 16000.0;
        for (unsigned phase=0; phase<3; ++phase) {
            double total=0;
            for (unsigned k=0; k<size; ++k) {
                const double distance=static_cast<double>(k)-64.0+static_cast<double>(phase)/3.0;
                const double sinc=distance==0 ? width :
                    std::sin(std::numbers::pi*width*distance)/(std::numbers::pi*distance);
                const double window=0.42-0.5*std::cos(2*std::numbers::pi*k/(size-1))+
                    0.08*std::cos(4*std::numbers::pi*k/(size-1));
                taps_[phase][k]=static_cast<float>(sinc*window);
                total+=taps_[phase][k];
            }
            for (auto& tap:taps_[phase]) tap=static_cast<float>(tap/total);
        }
    }
    void reset() { history_.fill(0); position_=0; phase_=0; }
    bool push(float sample, float& output) {
        history_[position_]=sample;
        position_=(position_+1)%size;
        const unsigned phase=phase_;
        phase_=(phase_+1)%4;
        if (phase==3) return false;
        output=0;
        unsigned index=position_;
        for (unsigned k=0; k<size; ++k) {
            index=index ? index-1 : size-1;
            output+=history_[index]*taps_[phase][k];
        }
        return true;
    }
private:
    static constexpr unsigned size=129;
    std::array<std::array<float,size>,3> taps_{};
    std::array<float,size> history_{};
    unsigned position_{0}, phase_{0};
};

// Peak-hold normalization does not increase gain during short Morse pauses.
// Stronger signals reduce gain immediately; gain
// recovers only after a one-second hold, with a ten-second release constant.
class DigitalGain {
public:
    void reset() { peak_=0.02F; hold_=0; }
    float push(float sample) {
        const float magnitude=std::abs(sample);
        if (magnitude>=peak_) { peak_=magnitude; hold_=16000; }
        else if (hold_) --hold_;
        else peak_*=0.99999375F;
        return sample*std::min(200.0F,0.15F/std::max(peak_,0.00001F));
    }
private:
    float peak_{0.02F};
    unsigned hold_{0};
};
}

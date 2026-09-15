#include "hamsdr/audio_processing.hpp"
#include <cmath>
#include <numbers>
#include <random>
#include <iostream>

int main() {
    hamsdr::AudioProcessing notch(true,0), nr(false,4), bypass;
    std::mt19937 rng(45);
    std::normal_distribution<float> noise(0,0.12F);
    double before=0, after=0, notch_power=0, tone_power=0;
    for (int i=0;i<64000;++i) {
        const float tone=0.2F*std::sin(2*std::numbers::pi_v<float>*1000*static_cast<float>(i)/16000);
        const float input=tone+noise(rng), reduced=nr.push(input), filtered=notch.push(tone);
        if (bypass.push(input)!=input) return 1;
        if (i>16000) {
            before+=(input-tone)*(input-tone); after+=(reduced-tone)*(reduced-tone);
            notch_power+=filtered*filtered; tone_power+=tone*tone;
        }
        if (!std::isfinite(reduced) || !std::isfinite(filtered)) return 2;
    }
    std::cout<<"Noise improvement dB "<<10*std::log10(before/after)
             <<"; notch rejection dB "<<10*std::log10(tone_power/notch_power)<<'\n';
    return after<before*0.5 && notch_power<tone_power*0.01 ? 0 : 3;
}

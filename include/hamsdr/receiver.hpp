#pragma once
#include <complex>
#include <span>
#include <string>
#include <vector>
#include <cstdint>
#include "hamsdr/audio_processing.hpp"

namespace hamsdr {
// Fixed-rate first milestone: 1.024 MHz complex input, 16 kHz mono output.
// Cascaded FIR decimation avoids filtering every sample with a narrow filter.
class Receiver {
public:
    Receiver(double offset, std::string mode, float low, float high, float squelch, bool notch=false, unsigned nr=0);
    std::vector<std::int16_t> push(std::span<const std::complex<float>> iq);
    float power_db() const;
private:
    struct Fir {
        std::vector<std::complex<float>> taps, history;
        std::size_t position{0};
        unsigned count{0}, decimation;
        Fir(unsigned size, float low, float high, float rate, unsigned decimation);
        bool push(std::complex<float> x, std::complex<float>& out);
    };
    Fir first_, second_, channel_;
    std::complex<float> oscillator_{1,0}, rotation_, previous_{1,0};
    std::string mode_;
    float squelch_, power_{1e-12F}, envelope_{0.02F}, dc_x_{0}, dc_y_{0}, deemphasis_{0};
    unsigned phase_count_{0};
    unsigned settling_samples_{512};
    AudioProcessing processing_;
};
}

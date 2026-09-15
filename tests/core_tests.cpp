#include "hamsdr/iq.hpp"
#include "hamsdr/spectrum.hpp"

#include <algorithm>
#include <cmath>
#include <complex>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <numbers>
#include <vector>

namespace {
void require(bool condition, const char* message) {
    if (!condition) { std::cerr << "FAIL: " << message << '\n'; std::exit(1); }
}

void test_iq_conversion() {
    const std::vector<std::uint8_t> bytes{0, 255, 128, 127, 42};
    const auto iq = hamsdr::convert_u8_iq(bytes);
    require(iq.size() == 2, "trailing partial IQ sample must be ignored");
    require(std::abs(iq[0].real() + 1.0F) < 0.001F, "minimum I conversion");
    require(std::abs(iq[0].imag() - 1.0F) < 0.001F, "maximum Q conversion");
    require(iq[1].real() > 0.0F && iq[1].imag() < 0.0F, "midpoint polarity");
}

void test_spectrum_tone() {
    constexpr std::size_t size = 1024;
    constexpr std::size_t positive_bin = 128;
    std::vector<std::complex<float>> samples(size);
    for (std::size_t i = 0; i < size; ++i) {
        const float phase = 2.0F * std::numbers::pi_v<float> *
                            static_cast<float>(positive_bin * i) / static_cast<float>(size);
        samples[i] = std::polar(0.8F, phase);
    }
    hamsdr::SpectrumEngine engine({size, 1, -100.0F, 0.0F});
    const auto frames = engine.push(samples);
    require(frames.size() == 1, "one complete FFT must produce one frame");
    const auto peak = std::max_element(frames[0].begin(), frames[0].end());
    const auto peak_bin = static_cast<std::size_t>(std::distance(frames[0].begin(), peak));
    require(peak_bin == size / 2 + positive_bin, "fft-shifted complex tone bin");
    const auto row = engine.quantize(frames[0]);
    require(row.size() == size, "waterfall row width");
    require(row[peak_bin] > 200, "strong tone should quantize near the top");
}
}

int main() {
    test_iq_conversion();
    test_spectrum_tone();
    std::cout << "All core tests passed\n";
    return 0;
}

#include "hamsdr/spectrum.hpp"

#include <fftw3.h>

#include <algorithm>
#include <cmath>
#include <numbers>
#include <stdexcept>

namespace hamsdr {
namespace {
bool is_power_of_two(std::size_t value) { return value >= 2 && (value & (value - 1)) == 0; }
}

SpectrumEngine::SpectrumEngine(SpectrumConfig config)
    : config_(config), input_(config.fft_size), output_(config.fft_size),
      window_(config.fft_size), accumulator_(config.fft_size, 0.0F) {
    if (!is_power_of_two(config_.fft_size)) throw std::invalid_argument("fft_size must be a power of two");
    if (config_.averages == 0) throw std::invalid_argument("averages must be at least one");
    if (!(config_.floor_db < config_.ceiling_db)) throw std::invalid_argument("floor_db must be below ceiling_db");

    const float denominator = static_cast<float>(config_.fft_size - 1);
    for (std::size_t i = 0; i < window_.size(); ++i) {
        const float phase = 2.0F * std::numbers::pi_v<float> * static_cast<float>(i) / denominator;
        window_[i] = 0.5F - 0.5F * std::cos(phase);
    }
    plan_ = fftwf_plan_dft_1d(static_cast<int>(config_.fft_size),
        reinterpret_cast<fftwf_complex*>(input_.data()),
        reinterpret_cast<fftwf_complex*>(output_.data()), FFTW_FORWARD, FFTW_ESTIMATE);
    if (plan_ == nullptr) throw std::runtime_error("unable to create FFTW plan");
    pending_.reserve(config_.fft_size * 2);
}

SpectrumEngine::~SpectrumEngine() { if (plan_ != nullptr) fftwf_destroy_plan(plan_); }

std::vector<float> SpectrumEngine::transform_power_frame() {
    for (std::size_t i = 0; i < config_.fft_size; ++i)
        input_[i] = pending_[pending_offset_ + i] * window_[i];
    fftwf_execute(plan_);
    std::vector<float> frame(config_.fft_size);
    const float normalization = 1.0F / static_cast<float>(config_.fft_size);
    const std::size_t half = config_.fft_size / 2;
    for (std::size_t i = 0; i < config_.fft_size; ++i) {
        const std::size_t shifted = (i + half) % config_.fft_size;
        const float amplitude = std::abs(output_[shifted]) * normalization;
        frame[i] = amplitude * amplitude;
    }
    return frame;
}

std::vector<std::vector<float>> SpectrumEngine::push(std::span<const std::complex<float>> samples) {
    pending_.insert(pending_.end(), samples.begin(), samples.end());
    std::vector<std::vector<float>> ready;
    while (pending_.size() - pending_offset_ >= config_.fft_size) {
        auto frame = transform_power_frame();
        for (std::size_t i = 0; i < frame.size(); ++i) accumulator_[i] += frame[i];
        ++accumulated_;
        if (accumulated_ == config_.averages) {
            const float divisor = static_cast<float>(accumulated_);
            constexpr float epsilon = 1.0e-20F;
            for (float& value : accumulator_)
                value = 10.0F * std::log10(std::max(value / divisor, epsilon));
            ready.push_back(accumulator_);
            std::fill(accumulator_.begin(), accumulator_.end(), 0.0F);
            accumulated_ = 0;
        }
        pending_offset_ += config_.fft_size;
    }
    if (pending_offset_ > 0) {
        pending_.erase(pending_.begin(), pending_.begin() + static_cast<std::ptrdiff_t>(pending_offset_));
        pending_offset_ = 0;
    }
    return ready;
}

std::vector<std::uint8_t> SpectrumEngine::quantize(std::span<const float> db) const {
    std::vector<std::uint8_t> row;
    row.reserve(db.size());
    const float span = config_.ceiling_db - config_.floor_db;
    for (const float value : db) {
        const float normalized = std::clamp((value - config_.floor_db) / span, 0.0F, 1.0F);
        row.push_back(static_cast<std::uint8_t>(std::lround(normalized * 255.0F)));
    }
    return row;
}

}  // namespace hamsdr

#pragma once

#include <complex>
#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>

struct fftwf_plan_s;

namespace hamsdr {

struct SpectrumConfig {
    std::size_t fft_size{4096};
    std::size_t averages{4};
    float floor_db{-110.0F};
    float ceiling_db{-20.0F};
};

class SpectrumEngine {
public:
    explicit SpectrumEngine(SpectrumConfig config);
    ~SpectrumEngine();
    SpectrumEngine(const SpectrumEngine&) = delete;
    SpectrumEngine& operator=(const SpectrumEngine&) = delete;

    std::vector<std::vector<float>> push(std::span<const std::complex<float>> samples);
    std::vector<std::uint8_t> quantize(std::span<const float> db) const;
    [[nodiscard]] const SpectrumConfig& config() const noexcept { return config_; }

private:
    std::vector<float> transform_power_frame();

    SpectrumConfig config_;
    std::vector<std::complex<float>> pending_;
    std::size_t pending_offset_{0};
    std::vector<std::complex<float>> input_;
    std::vector<std::complex<float>> output_;
    std::vector<float> window_;
    std::vector<float> accumulator_;
    std::size_t accumulated_{0};
    fftwf_plan_s* plan_{nullptr};
};

}  // namespace hamsdr

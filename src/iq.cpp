// SPDX-License-Identifier: GPL-3.0-only
#include "hamsdr/iq.hpp"

namespace hamsdr {

std::vector<std::complex<float>> convert_u8_iq(std::span<const std::uint8_t> bytes) {
    const std::size_t count = bytes.size() / 2;
    std::vector<std::complex<float>> result;
    result.reserve(count);
    constexpr float midpoint = 127.5F;
    constexpr float scale = 1.0F / midpoint;
    for (std::size_t i = 0; i < count; ++i) {
        const float in_phase = (static_cast<float>(bytes[i * 2]) - midpoint) * scale;
        const float quadrature = (static_cast<float>(bytes[i * 2 + 1]) - midpoint) * scale;
        result.emplace_back(in_phase, quadrature);
    }
    return result;
}

}  // namespace hamsdr

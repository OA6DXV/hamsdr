#pragma once

#include <complex>
#include <cstdint>
#include <span>
#include <vector>

namespace hamsdr {

std::vector<std::complex<float>> convert_u8_iq(std::span<const std::uint8_t> bytes);

}  // namespace hamsdr

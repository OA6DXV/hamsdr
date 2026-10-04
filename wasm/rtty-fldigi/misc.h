// SPDX-License-Identifier: GPL-3.0-or-later
// Adapted from Fldigi misc.h; copyright Dave Freese and Fldigi contributors.
#pragma once
#include <cmath>
inline double sinc(double x) {
    return std::fabs(x) < 1e-10 ? 1.0 : std::sin(M_PI*x)/(M_PI*x);
}
inline double decayavg(double average, double input, int weight) {
    return weight <= 1 ? input : average+(input-average)/weight;
}

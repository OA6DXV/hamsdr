#pragma once
#include <array>
#include <algorithm>

namespace hamsdr {
// Normalized LMS delayed predictor: predictable carriers can be subtracted
// (autonotch), or retained while uncorrelated noise is reduced (NR).
class AudioProcessing {
    struct Predictor {
        std::array<float, 64> weights{};
        std::array<float, 128> history{};
        std::size_t position{0};
        float push(float x, unsigned delay, float mu) {
            history[position] = x;
            float estimate=0, energy=0.0001F;
            for (unsigned k=0;k<weights.size();++k) {
                const float v=history[(position+history.size()-delay-k)%history.size()];
                estimate+=weights[k]*v; energy+=v*v;
            }
            const float error=x-estimate, gain=mu*error/energy;
            for (unsigned k=0;k<weights.size();++k) {
                const float v=history[(position+history.size()-delay-k)%history.size()];
                weights[k]=std::clamp(weights[k]*0.999999F+gain*v,-2.0F,2.0F);
            }
            position=(position+1)%history.size();
            return estimate;
        }
    } notch_, noise_;
    bool notch_enabled_;
    unsigned reduction_;
public:
    AudioProcessing(bool notch=false, unsigned reduction=0)
        : notch_enabled_(notch), reduction_(std::min(reduction,4U)) {}
    float push(float x) {
        if (notch_enabled_) x-=notch_.push(x,24,0.04F);
        if (reduction_) {
            const float predicted=noise_.push(x,8,0.025F);
            const float mix=0.2F*static_cast<float>(reduction_);
            x=(1-mix)*x+mix*predicted;
        }
        return x;
    }
};
}

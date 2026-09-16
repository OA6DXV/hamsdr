#include "hamsdr/receiver.hpp"
#include <algorithm>
#include <cmath>
#include <numbers>
#include <stdexcept>

namespace hamsdr {
constexpr float pi = std::numbers::pi_v<float>;
Receiver::Fir::Fir(unsigned size, float low, float high, float rate, unsigned dec)
    : taps(size), history(size), decimation(dec) {
    retune(low,high,rate);
}
void Receiver::Fir::retune(float low, float high, float rate) {
    const float width = (high-low)/rate, center = (high+low)/(2*rate);
    for (unsigned i=0; i<taps.size(); ++i) {
        const float n = static_cast<float>(i) - static_cast<float>(taps.size()-1)/2;
        const float sinc = n == 0 ? width : std::sin(pi*width*n)/(pi*n);
        const float window = 0.42F-0.5F*std::cos(2*pi*static_cast<float>(i)/static_cast<float>(taps.size()-1))
                             +0.08F*std::cos(4*pi*static_cast<float>(i)/static_cast<float>(taps.size()-1));
        const float phase=2*pi*center*n;
        taps[i] = sinc*window*std::complex<float>(std::cos(phase),std::sin(phase));
    }
}
bool Receiver::Fir::push(std::complex<float> x, std::complex<float>& out) {
    history[position] = x;
    position = (position+1)%history.size();
    if (++count < decimation) return false;
    count=0; out={};
    auto p=position;
    for (std::size_t k=0; k<taps.size(); ++k) {
        p = p ? p-1 : history.size()-1;
        out += history[p]*taps[k];
    }
    return true;
}
Receiver::Receiver(double offset, std::string mode, float low, float high, float squelch, bool notch, unsigned nr)
    : first_(127,-40000,40000,1024000,8), second_(127,-6500,6500,128000,8),
      channel_(257,low,high,16000,1),
      rotation_(std::polar(1.0F, static_cast<float>(-2*std::numbers::pi*offset/1024000))),
      offset_(offset), mode_(std::move(mode)), squelch_(squelch), processing_(notch,nr) {
    if (!std::isfinite(offset) || std::abs(offset)>512000 || !std::isfinite(low) || !std::isfinite(high) ||
        low < -6000 || high > 6000 || high-low < 100 || !std::isfinite(squelch) ||
        (mode_!="AM" && mode_!="USB" && mode_!="LSB" && mode_!="CW" && mode_!="NFM"))
        throw std::invalid_argument("invalid receiver parameters");
}
void Receiver::configure(double offset, std::string mode, float low, float high, float squelch, bool notch, unsigned nr) {
    if (!std::isfinite(offset) || std::abs(offset)>512000 || !std::isfinite(low) || !std::isfinite(high) ||
        low < -6000 || high > 6000 || high-low < 100 || !std::isfinite(squelch) || nr>4 ||
        (mode!="AM" && mode!="USB" && mode!="LSB" && mode!="CW" && mode!="NFM"))
        throw std::invalid_argument("invalid receiver parameters");
    if (offset!=offset_ || mode!=mode_) {previous_={1,0};deemphasis_=0;}
    offset_=offset;mode_=std::move(mode);squelch_=squelch;
    rotation_=std::polar(1.0F,static_cast<float>(-2*std::numbers::pi*offset/1024000));
    channel_.retune(low,high,16000);processing_.configure(notch,nr);
}
float Receiver::power_db() const { return 10*std::log10(std::max(power_,1e-20F)); }
std::vector<std::int16_t> Receiver::push(std::span<const std::complex<float>> iq) {
    std::vector<std::int16_t> audio; audio.reserve(iq.size()/64+1);
    for (auto x: iq) {
        const auto shifted=x*oscillator_;
        oscillator_ *= rotation_;
        if (++phase_count_==4096) { oscillator_/=std::abs(oscillator_); phase_count_=0; }
        std::complex<float> a,b,c;
        if (!first_.push(shifted,a) || !second_.push(a,b) || !channel_.push(b,c)) continue;
        power_ += 0.002F*(std::norm(c)-power_);
        float sample;
        if (mode_=="AM") sample=std::abs(c);
        else if (mode_=="NFM") {
            const auto product=c*std::conj(previous_);
            // Phase is undefined during FIR startup or absent carrier; avoid
            // large spurious impulses driving the slow AGC envelope.
            sample=(std::norm(c)>1e-8F && std::norm(previous_)>1e-8F)
                ?std::atan2(product.imag(),product.real()):0.0F;
            previous_=c;
            deemphasis_ += 0.35F*(sample-deemphasis_); sample=deemphasis_;
        } else sample=c.real();
        const float dc=sample-dc_x_+0.995F*dc_y_;
        dc_x_=sample; dc_y_=dc; sample=dc;
        // Fill the cascaded FIR/DC state before training the AGC. This also
        // gives retuning a short silent transition instead of a click.
        if (settling_samples_>0) {--settling_samples_;audio.push_back(0);continue;}
        const float magnitude=std::abs(sample);
        envelope_ += (magnitude>envelope_?0.02F:0.00005F)*(magnitude-envelope_);
        sample *= std::min(200.0F,0.15F/std::max(envelope_,0.00001F));
        sample = processing_.push(sample);
        if (power_db()<squelch_) sample=0;
        audio.push_back(static_cast<std::int16_t>(std::clamp(sample,-0.95F,0.95F)*32767));
    }
    return audio;
}
}

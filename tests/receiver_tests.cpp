#include "hamsdr/receiver.hpp"
#include <cmath>
#include <numbers>
#include <iostream>
#include <stdexcept>

void require(bool x,const char* message){if(!x)throw std::runtime_error(message);}
std::vector<std::int16_t> signal(const std::string& mode,float low,float high,double tone,bool am=false,float squelch=-150) {
    hamsdr::Receiver receiver(12300,mode,low,high,squelch);
    std::vector<std::int16_t> audio;
    for(unsigned base=0;base<512000;base+=1024){
        std::vector<std::complex<float>> iq(1024);
        for(unsigned j=0;j<iq.size();++j){
            const double t=(base+j)/1024000.0;
            const bool fm=mode=="NFM";
            const double phase=2*std::numbers::pi*(12300+(am||fm?0:tone))*t+
                (fm?0.5*std::sin(2*std::numbers::pi*tone*t):0);
            const float magnitude=am?static_cast<float>(0.2*(1+0.5*std::cos(2*std::numbers::pi*tone*t))):0.2F;
            iq[j]=magnitude*std::complex<float>(std::cos(phase),std::sin(phase));
        }
        auto chunk=receiver.push(iq); audio.insert(audio.end(),chunk.begin(),chunk.end());
    }
    return audio;
}
double amplitude(const std::vector<std::int16_t>& audio,double hz){
    std::complex<double> sum{};
    for(std::size_t i=2000;i<audio.size();++i){
        const double phase=-2*std::numbers::pi*hz*static_cast<double>(i)/16000;
        sum+=static_cast<double>(audio[i])*std::complex<double>(std::cos(phase),std::sin(phase));
    }
    return std::abs(sum)/static_cast<double>(audio.size()-2000);
}
int main(){
    for(const auto mode:{"USB","LSB","AM","CW","NFM"}){
        const bool lsb=std::string(mode)=="LSB",am=std::string(mode)=="AM",cw=std::string(mode)=="CW";
        const bool fm=std::string(mode)=="NFM";
        const double tone=cw?700:1000;
        auto audio=signal(mode,fm?-5000:am?-4000:lsb?-2700:cw?450:300,fm?5000:am?4000:lsb?-300:cw?950:2700,lsb?-tone:tone,am);
        require(audio.size()==8000,"decimation output count");
        const auto fundamental=amplitude(audio,tone),other=amplitude(audio,2100);
        std::cout<<mode<<" tone="<<fundamental<<" unwanted="<<other<<'\n';
        require(fundamental>500,"demodulated tone missing");
        require(fundamental>other*30,"unexpected output distortion");
    }
    auto rejected=signal("USB",300,2700,-1000);
    require(amplitude(rejected,1000)<10,"opposite sideband not rejected");
    for(double interferer:{17000.0,129000.0}) {
        auto aliased=signal("USB",300,2700,interferer);
        require(amplitude(aliased,1000)<10,"decimation alias rejection failed");
    }
    auto muted=signal("USB",300,2700,1000,false,0);
    require(amplitude(muted,1000)==0,"squelch did not mute");
    bool threw=false;try{hamsdr::Receiver invalid(0,"USB",100,0,-150);}catch(const std::invalid_argument&){threw=true;}
    require(threw,"invalid filter accepted");
    std::cout<<"Receiver signal tests passed\n";
}

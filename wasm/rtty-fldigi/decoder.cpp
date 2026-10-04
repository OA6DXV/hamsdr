// SPDX-License-Identifier: GPL-3.0-only
// Receive-only adaptation of Fldigi's rtty.cxx: Nyquist filters, optimal ATC
// and asynchronous ITA2 timing. Original copyright (C) 2012 Dave Freese,
// W1HKJ and Stefan Fendt, DL1SMF; derived from Tomi Manninen's gmfsk.
// HamSDR adapter copyright (C) 2026 OA6DXV. Desktop UI/TX are not included.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>
#include "fftfilt.h"
#include "misc.h"

namespace {
constexpr char letters[33]="\0E\nA SIU\rDRJNFCKTZLWHYPQOBG MXV ";
constexpr char figures[33]="\0" "3\n- \a87\r$4',!:(5\")2#6019?& ./; ";
class Receiver {
public:
    double rate=12000,baud=45.45,shift=170,center=1000,offset=0,afcRange=50;
    bool reverse=false,afc=true,figuresMode=false;
    int symbolLength=264,rxState=0,counter=0,bitIndex=0,word=0;
    std::vector<uint8_t> bitBuffer;
    std::unique_ptr<fftfilt> markFilter,spaceFilter;
    double markPhase=0,spacePhase=0,markEnv=0,spaceEnv=0,markNoise=0,spaceNoise=0;
    double confidence=0,freqError=0;
    cmplx previousMark{},previousSpace{};
    std::string text;
    std::vector<uint8_t> recentFrames;
    int frameCount=0,goodFrames=0;

    Receiver(){configure(12000,45.45,170,1000,false,true,50);}
    void configure(double newRate,double newBaud,double newShift,double newCenter,bool newReverse,bool newAfc,double newRange){
        rate=std::clamp(newRate,8000.0,48000.0);baud=std::clamp(newBaud,23.0,300.0);
        shift=std::clamp(newShift,23.0,1000.0);center=std::clamp(newCenter,shift/2+1,rate/2-shift/2-1);
        reverse=newReverse;afc=newAfc;afcRange=std::clamp(newRange,0.0,100.0);
        symbolLength=std::max(8,static_cast<int>(rate/baud+.5));
        const int filterLength=baud>=200?256:baud>=150?512:1024;
        markFilter=std::make_unique<fftfilt>(baud/rate,filterLength);markFilter->rtty_filter(baud/rate);
        spaceFilter=std::make_unique<fftfilt>(baud/rate,filterLength);spaceFilter->rtty_filter(baud/rate);
        bitBuffer.assign(symbolLength,1);offset=0;rxState=0;counter=0;bitIndex=0;word=0;figuresMode=false;
        markPhase=spacePhase=markEnv=spaceEnv=markNoise=spaceNoise=confidence=freqError=0;
        previousMark=previousSpace=cmplx{};text.clear();recentFrames.clear();frameCount=goodFrames=0;
    }
    void frame(bool valid){
        ++frameCount;if(valid)++goodFrames;
        recentFrames.push_back(valid);if(recentFrames.size()>24)recentFrames.erase(recentFrames.begin());
    }
    double quality()const{
        return recentFrames.empty()?0:std::count(recentFrames.begin(),recentFrames.end(),1)/static_cast<double>(recentFrames.size());
    }
    char decode(int value){
        if(value==31){figuresMode=false;return 0;}
        if(value==27){figuresMode=true;return 0;}
        // Keep HamSDR's international ITA2 table/polarity, including CR/LF.
        return figuresMode?figures[value&31]:letters[value&31];
    }
    bool rx(bool bit){
        std::move(bitBuffer.begin()+1,bitBuffer.end(),bitBuffer.begin());bitBuffer.back()=bit;
        const bool mark=bitBuffer[symbolLength/2];
        switch(rxState){
        case 0:
            if(bitBuffer.front()&&!bitBuffer.back()){
                int correction=std::count(bitBuffer.begin(),bitBuffer.end(),1);
                if(std::abs(symbolLength/2-correction)<6){rxState=1;counter=std::max(1,correction);}
            }
            break;
        case 1:
            if(--counter==0){
                if(!mark){rxState=2;counter=symbolLength;bitIndex=word=0;}
                else{rxState=0;frame(false);}
            }
            break;
        case 2:
            if(--counter==0){word|=static_cast<int>(mark)<<bitIndex++;counter=symbolLength;}
            if(bitIndex==5)rxState=3;
            break;
        case 3:
            if(--counter==0){
                frame(mark);rxState=0;
                if(mark){const char value=decode(word);if(value&&value!='\a')text.push_back(value);return true;}
            }
            break;
        }
        return false;
    }
    cmplx mix(double &phase,double frequency,double input){
        const cmplx value=cmplx(std::cos(phase),std::sin(phase))*cmplx(input,input);
        phase-=2*M_PI*frequency/rate;if(phase< -2*M_PI)phase+=2*M_PI;return value;
    }
    void process(const float *samples,int count){
        text.clear();frameCount=goodFrames=0;
        for(int n=0;n<count;n++){
            cmplx *marks=nullptr,*spaces=nullptr;
            // HamSDR's normal LSB convention is lower-frequency MARK.
            markFilter->run(mix(markPhase,center+offset-shift/2,samples[n]),&marks);
            const int available=spaceFilter->run(mix(spacePhase,center+offset+shift/2,samples[n]),&spaces);
            for(int i=0;i<available;i++){
                const double mark=std::abs(marks[i]),space=std::abs(spaces[i]);
                markEnv=decayavg(markEnv,mark,mark>markEnv?symbolLength/4:symbolLength*16);
                markNoise=decayavg(markNoise,mark,mark<markNoise?symbolLength/4:symbolLength*48);
                spaceEnv=decayavg(spaceEnv,space,space>spaceEnv?symbolLength/4:symbolLength*16);
                spaceNoise=decayavg(spaceNoise,space,space<spaceNoise?symbolLength/4:symbolLength*48);
                const double floor=std::min(spaceNoise,markNoise);
                const double mc=std::max(floor,std::min(mark,markEnv)),sc=std::max(floor,std::min(space,spaceEnv));
                const double me=markEnv-floor,se=spaceEnv-floor;
                // Fldigi's optimal Automatic Threshold Correction, not a
                // fixed comparison of the two raw tone magnitudes.
                const double discriminator=(mc-floor)*me-(sc-floor)*se-.25*(me*me-se*se);
                const double evidence=std::abs(mark-space)/(mark+space+1e-12);
                confidence=decayavg(confidence,evidence,symbolLength);
                const bool valid=rx(reverse?discriminator<=0:discriminator>0);
                if(valid&&afc&&confidence>.25&&quality()>=.7){
                    const cmplx current=reverse?spaces[i]:marks[i],previous=reverse?previousSpace:previousMark;
                    // Phase-derived residual frequency replaces the desktop
                    // waterfall-dependent AFC metric; clamp to the same range.
                    const double error=std::arg(std::conj(previous)*current)*rate/(2*M_PI);
                    if(std::abs(error)<baud/2){freqError=decayavg(freqError,error,4);offset=std::clamp(offset+freqError*.25,-afcRange,afcRange);}
                }
                previousMark=marks[i];previousSpace=spaces[i];
            }
        }
    }
};
}

extern "C" {
Receiver *rtty_create(){return new Receiver;}
void rtty_destroy(Receiver *receiver){delete receiver;}
void rtty_configure(Receiver *receiver,double rate,double baud,double shift,double center,int reverse,int afc,double range){
    receiver->configure(rate,baud,shift,center,reverse,afc,range);
}
const char *rtty_process(Receiver *receiver,const float *samples,int count){
    if(count<0||count>65536)return "";
    receiver->process(samples,count);return receiver->text.c_str();
}
double rtty_metric(Receiver *receiver,int metric){
    switch(metric){
    case 0:return receiver->confidence;case 1:return receiver->offset;
    case 2:return receiver->quality();case 3:return receiver->recentFrames.size();
    case 4:return receiver->frameCount;case 5:return receiver->goodFrames;
    default:return 0;
    }
}
}

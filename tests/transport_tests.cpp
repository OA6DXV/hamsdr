#include "hamsdr/rtl_tcp_client.hpp"
#include <arpa/inet.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>
#include <array>
#include <chrono>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <vector>

using namespace std::chrono_literals;
void require(bool ok,const char* text){if(!ok)throw std::runtime_error(text);}
int main(){
    const int listener=socket(AF_INET,SOCK_STREAM,0);
    require(listener>=0,"socket");
    sockaddr_in address{};address.sin_family=AF_INET;address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
    require(bind(listener,reinterpret_cast<sockaddr*>(&address),sizeof(address))==0,"bind");
    socklen_t size=sizeof(address);getsockname(listener,reinterpret_cast<sockaddr*>(&address),&size);
    require(listen(listener,4)==0,"listen");
    std::mutex mutex;std::vector<std::uint8_t> received,expected,commands;
    std::vector<std::string> states;
    for(unsigned pass=0;pass<2;++pass)for(unsigned i=0;i<3000;++i)expected.push_back(static_cast<std::uint8_t>(i+pass));
    std::jthread peer([&](std::stop_token stop){
        for(unsigned pass=0;pass<3 && !stop.stop_requested();++pass){
            pollfd ready{listener,POLLIN,0};if(poll(&ready,1,5000)<=0)return;
            const int fd=accept(listener,nullptr,nullptr);if(fd<0)return;
            const std::array<std::uint8_t,12> header{'R','T','L','0',0,0,0,5,0,0,0,29};
            for(auto byte:header){send(fd,&byte,1,MSG_NOSIGNAL);std::this_thread::sleep_for(1ms);}
            std::array<std::uint8_t,20> initialization{};
            if(recv(fd,initialization.data(),initialization.size(),MSG_WAITALL)!=static_cast<ssize_t>(initialization.size())){close(fd);return;}
            commands.insert(commands.end(),initialization.begin(),initialization.end());
            if(pass==0){
                // A connected source without IQ must time out and reconnect.
                std::this_thread::sleep_for(3300ms);
            }else{
                std::vector<std::uint8_t> payload(3001);
                for(unsigned i=0;i<payload.size();++i)payload[i]=static_cast<std::uint8_t>(i+pass-1);
                const std::array<unsigned,5> chunks{1,3,1001,1995,1};unsigned offset=0;
                for(auto n:chunks){send(fd,payload.data()+offset,n,MSG_NOSIGNAL);offset+=n;std::this_thread::sleep_for(2ms);}
            }
            // The final unmatched I byte must not leak into the next connection.
            close(fd);
        }
    });
    hamsdr::RtlTcpConfig config;config.port=ntohs(address.sin_port);config.reconnect_min=20ms;config.reconnect_max=100ms;
    config.control_device=true;config.manual_gain=true;config.gain_tenth_db=-50;
    hamsdr::RtlTcpClient client(config,[&](auto bytes){std::scoped_lock lock(mutex);received.insert(received.end(),bytes.begin(),bytes.end());},
        [&](auto event){std::scoped_lock lock(mutex);states.emplace_back(event);});
    client.start();const auto deadline=std::chrono::steady_clock::now()+10s;
    while(std::chrono::steady_clock::now()<deadline){
        {std::scoped_lock lock(mutex);if(received.size()>=expected.size())break;}
        std::this_thread::sleep_for(10ms);
    }
    const auto before=std::chrono::steady_clock::now();client.stop();
    require(std::chrono::steady_clock::now()-before<1s,"shutdown exceeded one second");
    peer.request_stop();peer.join();close(listener);
    require(received==expected,"IQ byte alignment changed across TCP fragments/reconnections");
    require(client.metrics().connections>=3,"stalled input did not reconnect");
    const auto device=client.device();require(device && device->tuner_type==5 && device->gain_count==29,"RTL0 header parsing");
    std::vector<std::uint8_t> expected_commands;
    auto command=[&](std::uint8_t id,std::uint32_t value){expected_commands.push_back(id);const auto network=htonl(value);const auto* bytes=reinterpret_cast<const std::uint8_t*>(&network);expected_commands.insert(expected_commands.end(),bytes,bytes+4);};
    for(unsigned pass=0;pass<3;++pass){command(0x02,config.sample_rate);command(0x01,config.center_frequency);command(0x03,1);command(0x04,static_cast<std::uint32_t>(config.gain_tenth_db));}
    require(commands==expected_commands,"SDR initialization commands were not sent on every connection");
    std::cout<<"TCP fragments, incomplete pair, stall recovery and shutdown passed\n";
}

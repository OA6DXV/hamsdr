#include "hamsdr/rtl_tcp_client.hpp"

#include <arpa/inet.h>
#include <netdb.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>
#include <fcntl.h>

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstring>
#include <optional>
#include <stdexcept>
#include <vector>

namespace hamsdr {
namespace {
std::uint32_t read_be32(const std::uint8_t* value) {
    return (static_cast<std::uint32_t>(value[0]) << 24U) |
           (static_cast<std::uint32_t>(value[1]) << 16U) |
           (static_cast<std::uint32_t>(value[2]) << 8U) |
           static_cast<std::uint32_t>(value[3]);
}
}

RtlTcpClient::RtlTcpClient(RtlTcpConfig config, DataCallback data_callback, StateCallback state_callback)
    : config_(std::move(config)), data_callback_(std::move(data_callback)),
      state_callback_(std::move(state_callback)) {
    if (!data_callback_) throw std::invalid_argument("rtl_tcp data callback is required");
    if (config_.read_block_bytes < 2) throw std::invalid_argument("read block must contain at least one IQ sample");
    config_.read_block_bytes &= ~std::size_t{1};
}

RtlTcpClient::~RtlTcpClient() { stop(); }
void RtlTcpClient::start() {
    if (!worker_.joinable()) worker_ = std::jthread([this](std::stop_token stop) { run(stop); });
}
void RtlTcpClient::stop() {
    if (worker_.joinable()) { worker_.request_stop(); worker_.join(); }
}
bool RtlTcpClient::running() const noexcept { return worker_.joinable(); }
RtlTcpMetrics RtlTcpClient::metrics() const noexcept {
    return {connections_.load(), disconnects_.load(), bytes_received_.load()};
}
std::optional<RtlTcpDevice> RtlTcpClient::device() const {
    std::scoped_lock lock(device_mutex_);
    return device_;
}
void RtlTcpClient::state(std::string_view value) const { if (state_callback_) state_callback_(value); }

int RtlTcpClient::connect_socket(std::stop_token stop) const {
    addrinfo hints{};
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    hints.ai_protocol = IPPROTO_TCP;
    addrinfo* addresses = nullptr;
    const std::string service = std::to_string(config_.port);
    if (getaddrinfo(config_.host.c_str(), service.c_str(), &hints, &addresses) != 0) return -1;
    int connected = -1;
    for (addrinfo* current = addresses; current != nullptr && !stop.stop_requested(); current = current->ai_next) {
        const int fd = ::socket(current->ai_family, current->ai_socktype | SOCK_NONBLOCK | SOCK_CLOEXEC, current->ai_protocol);
        if (fd < 0) continue;
        bool ok = ::connect(fd, current->ai_addr, current->ai_addrlen) == 0;
        if (!ok && errno == EINPROGRESS) {
            const auto until = std::chrono::steady_clock::now() + std::chrono::seconds(3);
            while (!stop.stop_requested() && std::chrono::steady_clock::now()<until) {
                pollfd item{fd,POLLOUT,0};
                const int ready=::poll(&item,1,100);
                if (ready<0 && errno==EINTR) continue;
                if (ready<0) break;
                if (ready>0) {
                    int error=0; socklen_t length=sizeof(error);
                    ok=::getsockopt(fd,SOL_SOCKET,SO_ERROR,&error,&length)==0 && error==0;
                    break;
                }
            }
        }
        if (ok) {connected=fd;break;}
        ::close(fd);
    }
    freeaddrinfo(addresses);
    return connected;
}

bool RtlTcpClient::read_exact(int fd, std::span<std::uint8_t> destination, std::stop_token stop) const {
    std::size_t offset = 0;
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(3);
    while (offset < destination.size() && !stop.stop_requested()) {
        if (std::chrono::steady_clock::now()>deadline) return false;
        pollfd item{fd, POLLIN, 0};
        const int ready = ::poll(&item, 1, 250);
        if (ready < 0 && errno == EINTR) continue;
        if (ready < 0) return false;
        if (ready == 0) continue;
        const ssize_t count = ::recv(fd, destination.data() + offset, destination.size() - offset, 0);
        if (count<0 && (errno==EINTR || errno==EAGAIN)) continue;
        if (count <= 0) return false;
        offset += static_cast<std::size_t>(count);
    }
    return offset == destination.size();
}

bool RtlTcpClient::write_command(int fd, std::uint8_t command, std::uint32_t value) const {
    const std::uint32_t network_value = htonl(value);
    std::array<std::uint8_t, 5> message{};
    message[0] = command;
    std::memcpy(message.data() + 1, &network_value, sizeof(network_value));
    return ::send(fd, message.data(), message.size(), MSG_NOSIGNAL) == static_cast<ssize_t>(message.size());
}

bool RtlTcpClient::initialize_connection(int fd, std::stop_token stop) {
    std::array<std::uint8_t, 12> header{};
    constexpr std::array<std::uint8_t, 4> magic{'R', 'T', 'L', '0'};
    if (!read_exact(fd, header, stop) || !std::equal(magic.begin(), magic.end(), header.begin())) {
        state("invalid-header");
        return false;
    }
    {
        std::scoped_lock lock(device_mutex_);
        device_ = RtlTcpDevice{read_be32(header.data() + 4), read_be32(header.data() + 8)};
    }
    if (config_.control_device) {
        if (!write_command(fd, 0x02, config_.sample_rate) ||
            !write_command(fd, 0x01, config_.center_frequency) ||
            !write_command(fd, 0x03, config_.manual_gain ? 1U : 0U)) return false;
        if (config_.manual_gain &&
            !write_command(fd, 0x04, static_cast<std::uint32_t>(config_.gain_tenth_db))) return false;
    }
    return true;
}

void RtlTcpClient::run(std::stop_token stop) {
    auto delay = config_.reconnect_min;
    std::vector<std::uint8_t> buffer(config_.read_block_bytes + 1);
    while (!stop.stop_requested()) {
        state("connecting");
        const int fd = connect_socket(stop);
        if (fd < 0) {
            state("connect-failed");
        } else if (initialize_connection(fd, stop)) {
            ++connections_;
            state("streaming");
            delay = config_.reconnect_min;
            std::optional<std::uint8_t> carry;
            auto last_data=std::chrono::steady_clock::now();
            while (!stop.stop_requested()) {
                if (std::chrono::steady_clock::now()-last_data>std::chrono::seconds(3)) {
                    state("stalled"); break;
                }
                pollfd item{fd, POLLIN, 0};
                const int ready = ::poll(&item, 1, 250);
                if (ready < 0 && errno == EINTR) continue;
                if (ready < 0 || (ready > 0 && !(item.revents & POLLIN) && (item.revents & (POLLERR | POLLHUP | POLLNVAL)))) break;
                if (ready == 0 || !(item.revents & POLLIN)) continue;
                const std::size_t prefix = carry.has_value() ? 1U : 0U;
                if (carry.has_value()) buffer[0] = *carry;
                const ssize_t count = ::recv(fd, buffer.data() + prefix,
                                             config_.read_block_bytes, 0);
                if (count<0 && (errno==EINTR || errno==EAGAIN)) continue;
                if (count <= 0) break;
                last_data=std::chrono::steady_clock::now();
                bytes_received_ += static_cast<std::size_t>(count);
                std::size_t complete = prefix + static_cast<std::size_t>(count);
                if ((complete & 1U) != 0U) {
                    carry = buffer[complete - 1];
                    --complete;
                } else {
                    carry.reset();
                }
                if (complete > 0)
                    data_callback_(std::span<const std::uint8_t>(buffer.data(), complete));
            }
            if (!stop.stop_requested()) { ++disconnects_; state("disconnected"); }
        }
        if (fd >= 0) { ::shutdown(fd, SHUT_RDWR); ::close(fd); }
        if (stop.stop_requested()) break;
        const auto deadline = std::chrono::steady_clock::now() + delay;
        while (!stop.stop_requested() && std::chrono::steady_clock::now() < deadline)
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        delay = std::min(delay * 2, config_.reconnect_max);
    }
    state("stopped");
}

}  // namespace hamsdr

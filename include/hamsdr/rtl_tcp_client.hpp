#pragma once

#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <mutex>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <thread>

namespace hamsdr {

struct RtlTcpConfig {
    std::string host{"127.0.0.1"};
    std::uint16_t port{1231};
    std::uint32_t center_frequency{7'100'500};
    std::uint32_t sample_rate{1'024'000};
    bool control_device{false};
    std::size_t read_block_bytes{32 * 1024};
    std::chrono::milliseconds reconnect_min{250};
    std::chrono::milliseconds reconnect_max{10'000};
};

struct RtlTcpDevice { std::uint32_t tuner_type{}; std::uint32_t gain_count{}; };
struct RtlTcpMetrics { std::uint64_t connections{}; std::uint64_t disconnects{}; std::uint64_t bytes_received{}; };

class RtlTcpClient {
public:
    using DataCallback = std::function<void(std::span<const std::uint8_t>)>;
    using StateCallback = std::function<void(std::string_view)>;

    RtlTcpClient(RtlTcpConfig config, DataCallback data_callback, StateCallback state_callback = {});
    ~RtlTcpClient();
    RtlTcpClient(const RtlTcpClient&) = delete;
    RtlTcpClient& operator=(const RtlTcpClient&) = delete;

    void start();
    void stop();
    [[nodiscard]] bool running() const noexcept;
    [[nodiscard]] RtlTcpMetrics metrics() const noexcept;
    [[nodiscard]] std::optional<RtlTcpDevice> device() const;

private:
    void run(std::stop_token stop);
    int connect_socket(std::stop_token stop) const;
    bool initialize_connection(int fd, std::stop_token stop);
    bool read_exact(int fd, std::span<std::uint8_t> destination, std::stop_token stop) const;
    bool write_command(int fd, std::uint8_t command, std::uint32_t value) const;
    void state(std::string_view value) const;

    RtlTcpConfig config_;
    DataCallback data_callback_;
    StateCallback state_callback_;
    std::jthread worker_;
    std::atomic<std::uint64_t> connections_{0};
    std::atomic<std::uint64_t> disconnects_{0};
    std::atomic<std::uint64_t> bytes_received_{0};
    mutable std::mutex device_mutex_;
    std::optional<RtlTcpDevice> device_;
};

}  // namespace hamsdr

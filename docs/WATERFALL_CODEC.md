# Experimental waterfall protocol v2

The waterfall codec remains independent from the separately selectable PCM/ADPCM
audio profile. Browser binary kind `7` contains one independently
decodable row:

| Offset | Field | Encoding |
|---:|---|---|
| 0 | version | uint8, currently 2 |
| 1 | profile | 1 mobile, 2 balanced, 3 raw, 4 classic |
| 2 | bins | uint16 little-endian |
| 4 | bits per bin | uint8 |
| 5 | sequence | uint32 little-endian |
| 9 | lower frequency | int32 little-endian Hz |
| 13 | span | uint32 little-endian Hz |
| 17 | samples | little-endian bit stream |

Input is one 65536-bin uint8 FFT row. The gateway first selects the requested
frequency window. Reduced profiles max-pool adjacent bins, then quantize and
pack fixed-width integers. Classic mode keeps 8-bit intensity and encodes small
spatial deltas as nibbles, with an absolute 8-bit escape for larger changes;
every row remains independently decodable. Max-pooling was selected to keep
narrow carriers visible; averaging could erase a carrier between bins. There
is no state inherited from another row, so loss, reconnect, and profile changes
recover immediately.

## Profiles and measured demo transport

| Profile | Representation | Rate | Measured waterfall |
|---|---|---:|---:|
| mobile | up to 1024 bins, 4 bit | 7.8 rows/s | 33.1 kbit/s |
| balanced | up to 2048 bins, 6 bit | 7.8 rows/s | 96.9 kbit/s |
| raw | up to 4096 bins, 8 bit | 7.8 rows/s | 256.7 kbit/s |

At full band the mobile packet is 529 bytes, balanced is 1553 bytes and raw is
4113 bytes, including the 17-byte header. Raw is lossless after server-side
max-pooling. Maximum amplitude error after quantization is 17 units for mobile
and about 4 units for balanced. At full band, the transmitted spacing is 1 kHz,
500 Hz and 250 Hz; zoom selects progressively finer bins from the shared
15.625 Hz source.

`auto` initially uses browser width, Save-Data, effective network type,
reported downlink and device memory. It also drops a level when bounded server
queues repeatedly congest, waits six stable five-second intervals before
recovering, and forces mobile if measured drawing time is persistently high.
Manual selection bypasses adaptation.

Display cadence is a separate per-client divisor applied before encoding and
queueing. Normal sends every row (about 7.8/s), slow sends every second row
(about 3.9/s), and very slow every sixth row (about 1.3/s). A slower selection
therefore reduces network and browser work proportionally.

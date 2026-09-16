# Waterfall protocol v2

The waterfall codec remains independent from the separately selectable PCM/Opus
audio profile. Browser binary kind `7` contains one independently
decodable row:

| Offset | Field | Encoding |
|---:|---|---|
| 0 | version | uint8, currently 2 |
| 1 | profile | 4 low, 5 balanced, 6 high definition, 7 connection slow |
| 2 | bins | uint16 little-endian |
| 4 | bits per bin | uint8 |
| 5 | sequence | uint32 little-endian |
| 9 | lower frequency | int32 little-endian Hz |
| 13 | span | uint32 little-endian Hz |
| 17 | samples | lossless differential byte stream |

Input is one 65536-bin uint8 FFT row. The gateway first selects the requested
frequency window. Reduced profiles max-pool adjacent bins. The connection-slow
mode packs 6-bit levels; every other mode keeps 8-bit intensity and encodes
small spatial deltas as nibbles, with an absolute 8-bit escape for larger changes;
every row remains independently decodable. Max-pooling was selected to keep
narrow carriers visible; averaging could erase a carrier between bins. There
is no state inherited from another row, so loss, reconnect, and profile changes
recover immediately.

## Profiles

| Profile | Representation | Normal rate |
|---|---|---:|
| connection slow | up to 1024 bins, 6 bit | 5 rows/s |
| low definition | up to 1024 bins, 8 bit | 7.8 rows/s |
| balanced | up to 2048 bins, 8 bit | 7.8 rows/s |
| high definition | up to 4096 bins, 8 bit | 7.8 rows/s |

Packet size varies with spectral detail except for the fixed-size connection-slow
profile. The 8-bit profiles are lossless after server-side max-pooling. At full band, transmitted spacing is 1 kHz for the
1024-bin modes, 500 Hz for balanced and 250 Hz for high definition; zoom selects
progressively finer bins from the shared 15.625 Hz source.

Every device initially uses balanced. `auto`, when selected manually, drops a
level when bounded server queues repeatedly congest, waits six stable
five-second intervals before recovering, and selects connection slow if
measured drawing time is persistently high. Manual selection bypasses adaptation.

Display cadence is selected per client before encoding and queueing. Normal
sends about 7.8 rows/s, slow 3.9 and very slow 1.3; the connection-slow profile
uses 5, 2.5 and 0.8 rows/s respectively. High definition additionally unlocks
an 11.7 rows/s high mode. A slower selection reduces network and browser work
proportionally.

// SPDX-License-Identifier: GPL-3.0-only
//! Offline regression replay; recordings remain outside the repository.
use hamsdr_cw_decoder::CwDecoder;
fn main() {
    let args: Vec<String> = std::env::args().collect();
    assert!(args.len() >= 2, "Usage: replay WAV [tone] [seed_wpm]");
    let wav = std::fs::read(&args[1]).expect("read recording");
    assert_eq!(&wav[..4], b"RIFF");
    assert_eq!(&wav[8..12], b"WAVE");
    let mut offset = 12;
    let mut pcm = None;
    let mut valid_format = false;
    while offset + 8 <= wav.len() {
        let length = u32::from_le_bytes(wav[offset + 4..offset + 8].try_into().unwrap()) as usize;
        let chunk = &wav[offset + 8..offset + 8 + length];
        match &wav[offset..offset + 4] {
            b"fmt " => {
                valid_format = chunk.len() >= 16
                    && chunk[..4] == [1, 0, 1, 0]
                    && u32::from_le_bytes(chunk[4..8].try_into().unwrap()) == 12000
                    && chunk[14..16] == [16, 0];
            }
            b"data" => pcm = Some(chunk),
            _ => (),
        }
        offset += 8 + length + (length & 1);
    }
    assert!(valid_format, "Expected mono PCM16 at 12 kHz");
    let mut decoder = CwDecoder::new(
        args.get(2).and_then(|v| v.parse().ok()).unwrap_or(700.0),
        args.get(3).and_then(|v| v.parse().ok()).unwrap_or(20.0),
    );
    let samples: Vec<f32> = pcm
        .expect("audio data")
        .as_chunks::<2>()
        .0
        .iter()
        .map(|p| i16::from_le_bytes([p[0], p[1]]) as f32 / 32768.0)
        .collect();
    let mut text = String::new();
    for chunk in samples.chunks(1200) {
        text.push_str(&decoder.process(chunk));
    }
    text.push_str(&decoder.process(&vec![0.0; 24000]));
    println!("{}", text.trim());
    eprintln!(
        "Final timing: {:.1} WPM; {} samples",
        decoder.wpm(),
        samples.len()
    );
}

// SPDX-License-Identifier: GPL-3.0-only
//! Browser adapter for cw-dit's streaming DSP and Morse decoder.
use cwdit_dsp::{Debouncer, Goertzel, MovingAverage, RunLengthEncoder, Threshold};
use cwdit_morse::{BootstrapDecoder, Decoded, TimingEstimator};
use wasm_bindgen::prelude::*;

const RATE: f32 = 12_000.0;

#[wasm_bindgen]
pub struct CwDecoder {
    filter: Goertzel,
    lower_guard: Goertzel,
    upper_guard: Goertzel,
    smooth: MovingAverage,
    lower_smooth: MovingAverage,
    upper_smooth: MovingAverage,
    threshold: Threshold,
    runs: RunLengthEncoder,
    debounce: Debouncer,
    morse: BootstrapDecoder,
    envelope_rate: f32,
    silent_ticks: u32,
    silence_flushed: bool,
    keyed: bool,
}

fn append(text: &mut String, events: impl IntoIterator<Item = Decoded>) {
    for event in events {
        match event {
            Decoded::Char(ch) => text.push(ch),
            Decoded::WordBreak => text.push(' '),
            Decoded::Unknown => text.push('·'),
        }
    }
}

#[wasm_bindgen]
impl CwDecoder {
    #[wasm_bindgen(constructor)]
    pub fn new(tone: f32, seed_wpm: f32) -> CwDecoder {
        let tone = if tone.is_finite() { tone.clamp(100.0, 5000.0) } else { 700.0 };
        let wpm = if seed_wpm.is_finite() { seed_wpm.clamp(5.0, 60.0) } else { 20.0 };
        let block = (RATE * 1.2 / wpm / 6.0).round().max((RATE / tone).ceil()) as u32;
        let envelope_rate = RATE / block as f32;
        let timing = TimingEstimator::from_wpm(wpm, envelope_rate);
        let unit = timing.unit();
        let spacing=RATE/block as f32;
        let upper=(tone+spacing*2.0).min(5900.0);
        let lower=if tone-spacing*2.0>=spacing { tone-spacing*2.0 } else { (tone+spacing*4.0).min(5900.0) };
        let smoothing=(unit/4).max(1) as usize;
        CwDecoder {
            filter: Goertzel::new(tone, RATE, block),
            lower_guard: Goertzel::new(lower,RATE,block),
            upper_guard: Goertzel::new(upper,RATE,block),
            smooth: MovingAverage::new(smoothing),
            lower_smooth: MovingAverage::new(smoothing),
            upper_smooth: MovingAverage::new(smoothing),
            threshold: Threshold::new(envelope_rate, 1.5, 0.0001).with_snr_gate(2.5),
            runs: RunLengthEncoder::new(),
            debounce: Debouncer::new((unit / 5).max(2)),
            morse: BootstrapDecoder::new(timing),
            envelope_rate,
            silent_ticks: 0,
            silence_flushed: false,
            keyed: false,
        }
    }

    /// Audio is already lossless mono PCM at 12 kHz; no playback resampling enters here.
    pub fn process(&mut self, samples: &[f32]) -> String {
        let mut text = String::new();
        for &sample in samples {
            let sample=if sample.is_finite(){sample}else{0.0};
            let lower=self.lower_guard.push(sample);
            let upper=self.upper_guard.push(sample);
            let Some(envelope) = self.filter.push(sample) else { continue };
            let envelope=self.smooth.push(envelope);
            let noise=(self.lower_smooth.push(lower.unwrap_or(0.0))+self.upper_smooth.push(upper.unwrap_or(0.0)))*0.5;
            // A keyed carrier must stand above adjacent frequency bins, not only an old peak.
            self.keyed = self.threshold.push(envelope)&&envelope>noise*4.0;
            if self.keyed { self.silent_ticks = 0; self.silence_flushed = false; }
            else { self.silent_ticks = self.silent_ticks.saturating_add(1); }
            if let Some(run) = self.runs.push(self.keyed) {
                if let Some(run) = self.debounce.push(run) {
                    append(&mut text, self.morse.push(run.mark, run.duration));
                }
            }
            // Deliver the final character during a long pause, without waiting for another tone.
            let flush_after=if self.morse.is_bootstrapped(){self.morse.timing().unit()*8}else{(self.envelope_rate*2.0) as u32};
            if !self.silence_flushed && self.silent_ticks >= flush_after {
                if let Some(run) = self.runs.finish() {
                    if let Some(run) = self.debounce.push(run) {
                        append(&mut text, self.morse.push(run.mark, run.duration));
                    }
                }
                if let Some(run) = self.debounce.finish() {
                    append(&mut text, self.morse.push(run.mark, run.duration));
                }
                if self.morse.is_bootstrapped() { append(&mut text, self.morse.finish()); }
                self.silence_flushed = true;
            }
        }
        text
    }

    pub fn wpm(&self) -> f32 { self.morse.timing().wpm(self.envelope_rate) }
    pub fn locked(&self) -> bool { self.morse.is_bootstrapped() }
    pub fn keyed(&self) -> bool { self.keyed }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture(wpm: f32, tone: f32, amplitude: f32) -> Vec<f32> {
        let mut audio = vec![0.0; RATE as usize];
        let unit = (RATE * 1.2 / wpm).round() as usize;
        // CQ DE W1AW; all timing and audio are generated independently of the decoder.
        for (word_index, word) in ["-.-. --.-", "-.. .", ".-- .---- .- .--"].iter().enumerate() {
            for (char_index, pattern) in word.split(' ').enumerate() {
                if char_index > 0 { audio.extend(vec![0.0; unit * 2]); }
                for mark in pattern.chars() {
                    for _ in 0..unit * if mark == '-' { 3 } else { 1 } {
                        audio.push(amplitude * (std::f32::consts::TAU * tone * audio.len() as f32 / RATE).sin());
                    }
                    audio.extend(vec![0.0; unit]);
                }
            }
            audio.extend(vec![0.0; unit * if word_index < 2 { 6 } else { 12 }]);
        }
        audio
    }
    #[test]
    fn decodes_chunked_pcm_at_multiple_speeds_and_levels() {
        for wpm in [5.0, 12.0, 20.0, 35.0, 60.0] {
            for amplitude in [0.02, 0.5] {
                let mut decoder = CwDecoder::new(700.0, wpm);
                let text: String = fixture(wpm, 700.0, amplitude).chunks(317).map(|chunk| decoder.process(chunk)).collect();
                assert_eq!(text.trim(), "CQ DE W1AW", "{wpm} WPM, amplitude {amplitude}");
            }
        }
    }
    #[test]
    fn silence_does_not_emit_characters() {
        let mut decoder = CwDecoder::new(700.0, 20.0);
        assert!(decoder.process(&vec![0.0; 12000 * 30]).is_empty());
    }
    #[test]
    fn decodes_with_noise_and_an_initial_speed_estimate() {
        let mut state=12345_u32;
        for wpm in [12.0, 28.0] {
            let mut audio=fixture(wpm, 700.0, 0.15);
            for sample in &mut audio {
                state=state.wrapping_mul(1664525).wrapping_add(1013904223);
                *sample+=((state >> 8) as f32 / 16777216.0 - 0.5)*0.01;
            }
            let mut decoder=CwDecoder::new(700.0,20.0);
            let text:String=audio.chunks(503).map(|chunk|decoder.process(chunk)).collect();
            assert_eq!(text.trim(),"CQ DE W1AW","{wpm} WPM with noise and 20 WPM seed");
        }
    }
    #[test]
    fn stationary_noise_does_not_emit_characters() {
        let mut state=45678_u32;
        let audio:Vec<f32>=(0..12000*30).map(|_|{
            state=state.wrapping_mul(1664525).wrapping_add(1013904223);
            ((state >> 8) as f32 / 16777216.0 - 0.5)*0.05
        }).collect();
        let mut decoder=CwDecoder::new(700.0,20.0);
        assert!(decoder.process(&audio).trim().is_empty());
    }
}

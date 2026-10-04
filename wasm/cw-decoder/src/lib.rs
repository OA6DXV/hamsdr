// SPDX-License-Identifier: GPL-3.0-only
//! Classic CW DSP and timing decoder, using cw-dit's slicer and alphabet.
use cwdit_dsp::{MovingAverage, QuantileSlicer, Run, RunLengthEncoder};
use cwdit_morse::Decoded;
use wasm_bindgen::prelude::*;
mod timing;
use timing::MorseTiming;

const RATE: f32 = 12_000.0;
const ENVELOPE_RATE: f32 = 600.0;

/// Merge short opposite-state glitches without shifting the total duration.
/// The rejection interval follows learned speed, rather than the initial seed.
#[derive(Default)]
struct RunDebouncer {
    pending: Option<Run>,
}
impl RunDebouncer {
    fn push(&mut self, run: Run, minimum: u32) -> Option<Run> {
        if let Some(pending) = &mut self.pending
            && (pending.mark == run.mark || run.duration < minimum)
        {
            pending.duration = pending.duration.saturating_add(run.duration);
            return None;
        }
        self.pending.replace(run)
    }
    fn finish(&mut self) -> Option<Run> {
        self.pending.take()
    }
}

/// Narrow audio selection and envelope sampling are independent: increasing
/// timing resolution must not admit more neighbouring carriers or white noise.
struct ToneEnvelope {
    phase: (f32, f32),
    step: (f32, f32),
    i: [f32; 4],
    q: [f32; 4],
    alpha: f32,
    ticks: u32,
}
impl ToneEnvelope {
    fn follow_width(&mut self, hz: f32) {
        let target = 1.0 - (-std::f32::consts::TAU * hz / RATE).exp();
        self.alpha += (target - self.alpha) * 0.05;
    }
    fn new(tone: f32) -> Self {
        let angle = std::f32::consts::TAU * tone / RATE;
        Self {
            phase: (1.0, 0.0),
            step: (angle.cos(), angle.sin()),
            i: [0.0; 4],
            q: [0.0; 4],
            alpha: 1.0 - (-std::f32::consts::TAU * 80.0 / RATE).exp(),
            ticks: 0,
        }
    }
    fn retune(&mut self, tone: f32) {
        let angle = std::f32::consts::TAU * tone / RATE;
        self.step = (angle.cos(), angle.sin());
    }
    fn push(&mut self, sample: f32) -> Option<f32> {
        let (c, s) = self.phase;
        let mut re = sample * c;
        let mut im = sample * s;
        for k in 0..4 {
            self.i[k] += self.alpha * (re - self.i[k]);
            self.q[k] += self.alpha * (im - self.q[k]);
            re = self.i[k];
            im = self.q[k];
        }
        self.phase = (
            c * self.step.0 - s * self.step.1,
            s * self.step.0 + c * self.step.1,
        );
        self.ticks += 1;
        if self.ticks < 20 {
            return None;
        }
        self.ticks = 0;
        let norm = self.phase.0.hypot(self.phase.1).max(1e-12);
        self.phase.0 /= norm;
        self.phase.1 /= norm;
        Some(re.hypot(im))
    }
}

#[wasm_bindgen]
pub struct CwDecoder {
    tone: f32,
    filter: ToneEnvelope,
    lower_guard: ToneEnvelope,
    upper_guard: ToneEnvelope,
    smooth: MovingAverage,
    lower_smooth: MovingAverage,
    upper_smooth: MovingAverage,
    threshold: QuantileSlicer,
    runs: RunLengthEncoder,
    debounce: RunDebouncer,
    morse: MorseTiming,
    envelope_rate: f32,
    silent_ticks: u32,
    silence_flushed: bool,
    keyed: bool,
    slicer_ticks: u32,
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
        let tone = if tone.is_finite() {
            tone.clamp(100.0, 5000.0)
        } else {
            700.0
        };
        let wpm = if seed_wpm.is_finite() {
            seed_wpm.clamp(5.0, 60.0)
        } else {
            20.0
        };
        let envelope_rate = ENVELOPE_RATE;
        let timing = MorseTiming::new(wpm);
        let spacing = 90.0;
        let upper = (tone + spacing * 2.0).min(5900.0);
        let lower = if tone - spacing * 2.0 >= spacing {
            tone - spacing * 2.0
        } else {
            (tone + spacing * 4.0).min(5900.0)
        };
        let smoothing = 3;
        CwDecoder {
            tone,
            filter: ToneEnvelope::new(tone),
            lower_guard: ToneEnvelope::new(lower),
            upper_guard: ToneEnvelope::new(upper),
            smooth: MovingAverage::new(smoothing),
            lower_smooth: MovingAverage::new(smoothing),
            upper_smooth: MovingAverage::new(smoothing),
            threshold: QuantileSlicer::new(envelope_rate, 48.0),
            runs: RunLengthEncoder::new(),
            debounce: RunDebouncer::default(),
            morse: timing,
            envelope_rate,
            silent_ticks: 0,
            silence_flushed: false,
            keyed: false,
            slicer_ticks: 0,
        }
    }

    /// Audio is already lossless mono PCM at 12 kHz; no playback resampling enters here.
    pub fn process(&mut self, samples: &[f32]) -> String {
        if self.morse.locked() {
            let width = (self.morse.wpm() * 3.5).clamp(35.0, 80.0);
            self.filter.follow_width(width);
            self.lower_guard.follow_width(width);
            self.upper_guard.follow_width(width);
        }
        let mut text = String::new();
        for &sample in samples {
            let sample = if sample.is_finite() { sample } else { 0.0 };
            let lower = self.lower_guard.push(sample);
            let upper = self.upper_guard.push(sample);
            let Some(envelope) = self.filter.push(sample) else {
                continue;
            };
            let envelope = self.smooth.push(envelope);
            let noise = self
                .lower_smooth
                .push(lower.unwrap_or(0.0))
                .min(self.upper_smooth.push(upper.unwrap_or(0.0)));
            // A keyed carrier must stand above adjacent frequency bins, not only an old peak.
            self.threshold.push(envelope);
            self.slicer_ticks = self.slicer_ticks.saturating_add(1);
            let floor = self.threshold.noise_level();
            let high = self.threshold.mark_level();
            // Lower hysteresis rails preserve weak dah edges under QSB. The
            // independent adjacent-band gate still rejects unkeyed noise.
            let sliced = self.slicer_ticks > 180
                && high > floor * 2.0
                && envelope > floor + (high - floor) * if self.keyed { 0.20 } else { 0.40 };
            self.keyed = sliced
                && envelope > noise * if self.keyed { 2.5 } else { 4.0 }
                && envelope > 0.00001;
            if self.keyed {
                self.silent_ticks = 0;
                self.silence_flushed = false;
            } else {
                self.silent_ticks = self.silent_ticks.saturating_add(1);
            }
            if let Some(run) = self.runs.push(self.keyed)
                && let Some(run) = self
                    .debounce
                    .push(run, (self.morse.unit() / 5).clamp(2, 24))
            {
                append(&mut text, self.morse.push(run.mark, run.duration));
            }
            // Deliver the final character during a long pause, without waiting for another tone.
            let flush_after = if self.morse.locked() {
                self.morse.unit() * 8
            } else {
                (self.envelope_rate * 2.0) as u32
            };
            if !self.silence_flushed && self.silent_ticks >= flush_after {
                if let Some(run) = self.runs.finish()
                    && let Some(run) = self
                        .debounce
                        .push(run, (self.morse.unit() / 5).clamp(2, 24))
                {
                    append(&mut text, self.morse.push(run.mark, run.duration));
                }
                if let Some(run) = self.debounce.finish() {
                    append(&mut text, self.morse.push(run.mark, run.duration));
                }
                if self.morse.locked() {
                    append(&mut text, self.morse.finish());
                }
                self.silence_flushed = true;
            }
        }
        text
    }

    pub fn wpm(&self) -> f32 {
        self.morse.wpm()
    }
    pub fn locked(&self) -> bool {
        self.morse.locked()
    }
    pub fn confidence(&self) -> f32 {
        self.morse.confidence()
    }
    pub fn keyed(&self) -> bool {
        self.keyed
    }

    /// AFC changes only frequency-selective filters, preserving learned timing.
    pub fn retune(&mut self, tone: f32) {
        if !tone.is_finite() {
            return;
        }
        let tone = tone.clamp(100.0, 5000.0);
        self.tone = tone;
        let spacing = 90.0;
        let lower = if tone - spacing * 2.0 >= spacing {
            tone - spacing * 2.0
        } else {
            (tone + spacing * 4.0).min(5900.0)
        };
        self.filter.retune(tone);
        self.lower_guard.retune(lower);
        self.upper_guard.retune((tone + spacing * 2.0).min(5900.0));
    }

    /// Discard a partial character across a transport gap, not the learned WPM.
    pub fn gap(&mut self) {
        self.morse.gap();
        self.runs = RunLengthEncoder::new();
        self.debounce = RunDebouncer::default();
        self.silent_ticks = 0;
        self.silence_flushed = false;
        self.keyed = false;
        self.retune(self.tone);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture(wpm: f32, tone: f32, amplitude: f32) -> Vec<f32> {
        let mut audio = vec![0.0; RATE as usize];
        let unit = (RATE * 1.2 / wpm).round() as usize;
        // CQ DE W1AW; all timing and audio are generated independently of the decoder.
        for (word_index, word) in ["-.-. --.-", "-.. .", ".-- .---- .- .--"]
            .iter()
            .enumerate()
        {
            for (char_index, pattern) in word.split(' ').enumerate() {
                if char_index > 0 {
                    audio.extend(vec![0.0; unit * 2]);
                }
                for mark in pattern.chars() {
                    for _ in 0..unit * if mark == '-' { 3 } else { 1 } {
                        audio.push(
                            amplitude
                                * (std::f32::consts::TAU * tone * audio.len() as f32 / RATE).sin(),
                        );
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
                let text: String = fixture(wpm, 700.0, amplitude)
                    .chunks(317)
                    .map(|chunk| decoder.process(chunk))
                    .collect();
                assert_eq!(
                    text.trim(),
                    "CQ DE W1AW",
                    "{wpm} WPM, amplitude {amplitude}"
                );
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
        let mut state = 12345_u32;
        for wpm in [12.0, 28.0] {
            let mut audio = fixture(wpm, 700.0, 0.15);
            for sample in &mut audio {
                state = state.wrapping_mul(1664525).wrapping_add(1013904223);
                *sample += ((state >> 8) as f32 / 16777216.0 - 0.5) * 0.01;
            }
            let mut decoder = CwDecoder::new(700.0, 20.0);
            let text: String = audio
                .chunks(503)
                .map(|chunk| decoder.process(chunk))
                .collect();
            assert_eq!(
                text.trim(),
                "CQ DE W1AW",
                "{wpm} WPM with noise and 20 WPM seed"
            );
        }
    }
    #[test]
    fn stationary_noise_does_not_emit_characters() {
        let mut state = 45678_u32;
        let audio: Vec<f32> = (0..12000 * 30)
            .map(|_| {
                state = state.wrapping_mul(1664525).wrapping_add(1013904223);
                ((state >> 8) as f32 / 16777216.0 - 0.5) * 0.05
            })
            .collect();
        let mut decoder = CwDecoder::new(700.0, 20.0);
        assert!(decoder.process(&audio).trim().is_empty());
    }
    #[test]
    fn afc_and_transport_gaps_preserve_learned_speed() {
        let mut decoder = CwDecoder::new(700.0, 20.0);
        decoder.process(&fixture(28.0, 700.0, 0.15));
        assert!(decoder.locked());
        let wpm = decoder.wpm();
        decoder.retune(720.0);
        assert!(decoder.locked());
        assert_eq!(decoder.wpm(), wpm);
        decoder.gap();
        assert!(decoder.locked());
        assert_eq!(decoder.wpm(), wpm);
        let text = decoder.process(&fixture(28.0, 720.0, 0.15));
        assert_eq!(text.trim(), "CQ DE W1AW");
        let mut fast = CwDecoder::new(700.0, 60.0);
        fast.retune(100.0);
        assert!(fast.process(&vec![0.0; 12000]).is_empty());
    }

    #[test]
    fn acquires_slow_and_fast_cw_without_knowing_the_speed() {
        for wpm in [5.0, 7.5, 8.0, 15.0, 28.0, 45.0, 60.0] {
            let mut decoder = CwDecoder::new(700.0, 20.0);
            let text: String = fixture(wpm, 700.0, 0.15)
                .chunks(317)
                .map(|chunk| decoder.process(chunk))
                .collect();
            assert_eq!(text.trim(), "CQ DE W1AW", "unknown {wpm} WPM");
            assert!((5.0..=60.0).contains(&decoder.wpm()));
        }
    }

    #[test]
    fn decodes_fading_cw_and_rejects_a_strong_neighbour() {
        let mut state = 12345_u32;
        let mut samples = fixture(18.0, 700.0, 0.20);
        for (index, sample) in samples.iter_mut().enumerate() {
            let t = index as f32 / RATE;
            *sample *= 0.55 + 0.4 * (std::f32::consts::TAU * 0.35 * t).cos();
            state = state.wrapping_mul(1664525).wrapping_add(1013904223);
            *sample += ((state >> 8) as f32 / 16777216.0 - 0.5) * 0.003;
            *sample += 0.25 * (std::f32::consts::TAU * 950.0 * t).sin();
        }
        let mut decoder = CwDecoder::new(700.0, 20.0);
        let text: String = samples
            .chunks(503)
            .map(|chunk| decoder.process(chunk))
            .collect();
        assert_eq!(
            text.trim(),
            "CQ DE W1AW",
            "QSB plus noise and nearby carrier"
        );
    }

    #[test]
    fn an_unkeyed_carrier_is_not_morse() {
        let mut decoder = CwDecoder::new(700.0, 20.0);
        let mut text = String::new();
        for offset in (0..12000 * 30).step_by(1200) {
            let samples: Vec<f32> = (offset..offset + 1200)
                .map(|i| 0.15 * (std::f32::consts::TAU * 700.0 * i as f32 / RATE).sin())
                .collect();
            text.push_str(&decoder.process(&samples));
        }
        text.push_str(&decoder.process(&vec![0.0; 24000]));
        assert!(text.trim().is_empty());
        assert!(!decoder.locked());
    }
}

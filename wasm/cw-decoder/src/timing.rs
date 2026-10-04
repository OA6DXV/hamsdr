// SPDX-License-Identifier: GPL-3.0-only
//! Bounded, signal-only timing estimation. No dictionary or callsign correction.
use cwdit_morse::{Decoded, alphabet};
use std::collections::VecDeque;

const RATE: f32 = 600.0;
pub struct MorseTiming {
    unit: f32,
    locked: bool,
    recent: VecDeque<(bool, u32)>,
    buffered: Vec<(bool, u32)>,
    marks: usize,
    pattern: String,
    pending: Option<u32>,
    word: bool,
    emitted: bool,
    confidence: f32,
}
impl MorseTiming {
    pub fn new(wpm: f32) -> Self {
        Self {
            unit: RATE * 1.2 / wpm,
            locked: false,
            recent: VecDeque::new(),
            buffered: Vec::new(),
            marks: 0,
            pattern: String::new(),
            pending: None,
            word: false,
            emitted: false,
            confidence: 0.0,
        }
    }
    pub fn unit(&self) -> u32 {
        self.unit.round() as u32
    }
    pub fn wpm(&self) -> f32 {
        RATE * 1.2 / self.unit
    }
    pub fn locked(&self) -> bool {
        self.locked
    }
    pub fn confidence(&self) -> f32 {
        self.confidence
    }
    pub fn gap(&mut self) {
        self.pending = None;
        self.pattern.clear();
        self.buffered.clear();
        if !self.locked {
            self.recent.clear();
            self.confidence = 0.0;
        }
        self.marks = 0;
        self.word = self.emitted;
    }
    fn error(mark: bool, duration: u32, next: Option<(bool, u32)>, unit: f32) -> f32 {
        let mut value = duration as f32 / unit;
        let targets: &[f32] = if mark {
            if let Some((false, gap)) = next {
                // Mark + intra-character gap survives symmetric edge erosion.
                if gap as f32 / unit > 0.4 && (gap as f32 / unit) < 1.9 {
                    value += gap as f32 / unit;
                    return [2.0_f32, 4.0]
                        .iter()
                        .map(|t| (value / t).ln().abs())
                        .fold(f32::INFINITY, f32::min);
                }
            }
            &[1.0, 3.0]
        } else {
            &[1.0, 3.0, 7.0]
        };
        targets
            .iter()
            .map(|t| (value / t).ln().abs())
            .fold(f32::INFINITY, f32::min)
    }
    fn score(&self, unit: f32) -> (f32, f32) {
        let mut total = 0;
        let mut good = 0;
        let mut cost = 0.0;
        let mut dits = 0;
        let mut dahs = 0;
        let mut intra = 0;
        for (index, &(mark, duration)) in self.recent.iter().enumerate() {
            if !mark && index == 0 {
                continue;
            }
            if mark && duration < 7 {
                continue;
            }
            let ratio = duration as f32 / unit;
            if mark && (0.55..1.65).contains(&ratio) {
                dits += 1;
            }
            if mark && (2.0..4.0).contains(&ratio) {
                dahs += 1;
            }
            if !mark && (0.4..1.8).contains(&ratio) {
                intra += 1;
            }
            let error = Self::error(mark, duration, self.recent.get(index + 1).copied(), unit);
            total += 1;
            if error < 0.40 {
                good += 1;
            }
            cost += error.min(0.8).powi(2);
        }
        if total == 0 {
            return (1.0, 0.0);
        }
        // A train of isolated noise blips is not evidence for a fast station.
        if dits < 3 || dahs < 2 || intra < 3 {
            return (1.0, 0.0);
        }
        // Outliers cannot drag the estimate toward increasingly fast noise.
        (cost / total as f32, good as f32 / total as f32)
    }
    fn estimate(&mut self) -> bool {
        let min = if self.locked {
            (self.unit * 0.8).max(12.0)
        } else {
            12.0
        };
        let max = if self.locked {
            (self.unit * 1.25).min(144.0)
        } else {
            144.0
        };
        let mut best = (f32::INFINITY, self.unit, 0.0);
        let mut unit = min;
        while unit <= max {
            let (cost, quality) = self.score(unit);
            let prior = if self.locked { 0.025 } else { 0.001 };
            let objective = cost + prior * (unit / self.unit).ln().powi(2);
            if objective < best.0 {
                best = (objective, unit, quality);
            }
            unit += 0.5;
        }
        // A stale estimate acquired during noise must not remain locked when
        // a real operator starts. Require a substantially better global fit.
        if self.locked && (best.2 < 0.72 || best.0 > 0.13) {
            let mut global = best;
            let mut candidate = 12.0;
            while candidate <= 144.0 {
                let (cost, quality) = self.score(candidate);
                if quality >= 0.85 && cost < global.0 {
                    global = (cost, candidate, quality);
                }
                candidate += 0.5;
            }
            if global.0 < best.0 * 0.65 {
                if global.1 < self.unit * 0.75 || global.1 > self.unit * 1.35 {
                    self.pattern.clear();
                    self.pending = None;
                    self.unit = global.1;
                }
                best = global;
            }
        }
        self.confidence = best.2;
        if best.2 < 0.72 {
            return false;
        }
        if self.locked {
            self.unit += 0.25 * (best.1 - self.unit);
        } else {
            self.unit = best.1;
            self.locked = true;
        }
        true
    }
    pub fn push(&mut self, mark: bool, duration: u32) -> Vec<Decoded> {
        self.recent.push_back((mark, duration));
        if self.recent.len() > 64 {
            self.recent.pop_front();
        }
        if mark {
            self.marks += 1;
        }
        if !self.locked {
            self.buffered.push((mark, duration));
            if self.buffered.len() > 128 {
                self.buffered.drain(..64);
            }
            if self.marks < 12 || mark || !self.estimate() {
                return Vec::new();
            }
            let mut output = Vec::new();
            for (m, d) in std::mem::take(&mut self.buffered) {
                self.decode(m, d, &mut output);
            }
            return output;
        }
        if !mark && self.marks >= 8 {
            self.estimate();
            self.marks = 0;
        } else if !mark && self.recent.len() >= 6 {
            self.confidence = self.score(self.unit).1;
        }
        let mut output = Vec::new();
        self.decode(mark, duration, &mut output);
        output
    }
    fn decode(&mut self, mark: bool, duration: u32, output: &mut Vec<Decoded>) {
        if mark {
            self.pending = Some(duration);
            return;
        }
        if let Some(mark) = self.pending.take() {
            let ratio = mark as f32 / self.unit;
            if ratio < 0.45 {
                return;
            }
            if ratio > 4.5 {
                self.pattern.clear();
                return;
            }
            let dit = if (duration as f32) < self.unit * 1.9 {
                ((mark + duration) as f32) < self.unit * 3.0
            } else {
                ratio < 2.0
            };
            if self.pattern.len() < 10 {
                self.pattern.push(if dit { '.' } else { '-' });
            }
        }
        if duration as f32 >= self.unit * 2.0 {
            self.flush(output);
        }
        if duration as f32 >= self.unit * 5.0 {
            self.word = self.emitted;
        }
    }
    fn flush(&mut self, output: &mut Vec<Decoded>) {
        if self.pattern.is_empty() {
            return;
        }
        if self.confidence >= 0.65 {
            if self.word {
                output.push(Decoded::WordBreak);
                self.word = false;
            }
            output.push(
                alphabet::char_for_pattern(&self.pattern)
                    .map(Decoded::Char)
                    .unwrap_or(Decoded::Unknown),
            );
            self.emitted = true;
        }
        self.pattern.clear();
    }
    pub fn finish(&mut self) -> Vec<Decoded> {
        let mut output = Vec::new();
        if let Some(mark) = self.pending.take() {
            self.decode(true, mark, &mut output);
            self.decode(false, self.unit() * 7, &mut output);
        }
        self.flush(&mut output);
        output
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn phrase(unit: u32, erosion: u32) -> Vec<(bool, u32)> {
        let mut runs = Vec::new();
        for (wi, word) in "CQ DE W1AW".split(' ').enumerate() {
            if wi > 0 {
                runs.push((false, unit * 7 + erosion));
            }
            for (ci, ch) in word.chars().enumerate() {
                if ci > 0 {
                    runs.push((false, unit * 3 + erosion));
                }
                for (mi, mark) in alphabet::pattern_for_char(ch).unwrap().chars().enumerate() {
                    if mi > 0 {
                        runs.push((false, unit + erosion));
                    }
                    runs.push((true, unit * if mark == '-' { 3 } else { 1 } - erosion));
                }
            }
        }
        runs.push((false, unit * 7));
        runs
    }
    fn text(events: Vec<Decoded>) -> String {
        events
            .into_iter()
            .map(|e| match e {
                Decoded::Char(c) => c,
                Decoded::WordBreak => ' ',
                Decoded::Unknown => '·',
            })
            .collect()
    }
    #[test]
    fn uses_mark_gap_period_to_survive_eroded_edges() {
        let mut timing = MorseTiming::new(20.0);
        let mut events = Vec::new();
        for (mark, duration) in phrase(48, 9) {
            events.extend(timing.push(mark, duration));
        }
        events.extend(timing.finish());
        assert_eq!(text(events).trim(), "CQ DE W1AW");
        assert!((timing.wpm() - 15.0).abs() < 1.0);
    }
    #[test]
    fn relearns_a_large_speed_change_without_a_worker_reset() {
        let mut timing = MorseTiming::new(20.0);
        for _ in 0..3 {
            for (m, d) in phrase(16, 0) {
                timing.push(m, d);
            }
        }
        assert!((timing.wpm() - 45.0).abs() < 1.0);
        let mut events = Vec::new();
        for _ in 0..6 {
            for (m, d) in phrase(48, 0) {
                events.extend(timing.push(m, d));
            }
        }
        assert!(text(events).contains("CQ DE W1AW"));
        assert!((timing.wpm() - 15.0).abs() < 1.0);
        assert!(
            timing.buffered.len() <= 128 && timing.recent.len() <= 64 && timing.pattern.len() <= 10
        );
    }
}

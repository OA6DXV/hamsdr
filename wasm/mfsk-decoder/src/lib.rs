// SPDX-License-Identifier: GPL-3.0-only
use js_sys::{Array, Object, Reflect, Uint8Array};
use wasm_bindgen::prelude::*;

use mfsk_core::ft4::Ft4;
use mfsk_core::ft8::Ft8;
use mfsk_core::msg::decode_request::DecodeRequest;
use mfsk_core::msg::wsjt77::unpack77;

fn set(object: &Object, key: &str, value: JsValue) {
    let _ = Reflect::set(object, &JsValue::from_str(key), &value);
}

fn row(mode: &str, timestamp_us: f64, snr: f32, dt: f32, hz: f32, text: String) -> Object {
    let object = Object::new();
    let seconds = timestamp_us / 1_000_000.0;
    let date = js_sys::Date::new(&JsValue::from_f64(seconds * 1000.0));
    let iso = date.to_iso_string().as_string().unwrap_or_default();
    let utc = iso.get(11..19).unwrap_or("");
    set(&object, "utc", JsValue::from_str(utc));
    set(&object, "mode", JsValue::from_str(mode));
    set(&object, "snr", JsValue::from_str(&format!("{snr:+.0}")));
    set(&object, "dt", JsValue::from_str(&format!("{dt:+.2}")));
    set(&object, "hz", JsValue::from_str(&format!("{hz:.0}")));
    set(&object, "text", JsValue::from_str(&text));
    object
}

fn pcm_from_bytes(bytes: &Uint8Array) -> Vec<i16> {
    let mut raw = vec![0u8; bytes.length() as usize];
    bytes.copy_to(&mut raw);
    raw.chunks_exact(2)
        .map(|pair| i16::from_le_bytes([pair[0], pair[1]]))
        .collect()
}

#[wasm_bindgen]
pub fn decode_slot(mode: &str, pcm: Uint8Array, timestamp_us: f64) -> Array {
    let audio = pcm_from_bytes(&pcm);
    let results = Array::new();
    match mode {
        "FT8" => {
            for result in DecodeRequest::<Ft8>::new(&audio, 100.0, 3000.0, 1.0, 50).decode().results {
                if let Some(text) = unpack77(result.message77()) {
                    results.push(&row("FT8", timestamp_us, result.snr_db, result.dt_sec, result.freq_hz, text).into());
                }
            }
        }
        "FT4" => {
            for result in DecodeRequest::<Ft4>::new(&audio, 100.0, 3000.0, 1.0, 50).decode().results {
                if let Some(text) = unpack77(result.message77()) {
                    results.push(&row("FT4", timestamp_us, result.snr_db, result.dt_sec, result.freq_hz, text).into());
                }
            }
        }
        _ => {}
    }
    results
}

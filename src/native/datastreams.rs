//! DSM pathway header codec: an 8-byte little-endian hash, then the pathway start and
//! current edge start (ms) as zigzag varints, base64-encoded (standard alphabet, padded).
//!
//! Byte-for-byte compatible with `ddtrace/internal/datastreams/encoding.py` and with the
//! other tracers that read and write this header.
use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use pyo3::{
    types::{PyBytes, PyBytesMethods as _, PyString, PyStringMethods as _},
    Bound, PyAny,
};

const MAX_VAR_LEN_64: usize = 9;

fn zigzag(v: i64) -> u64 {
    ((v >> 63) ^ (v << 1)) as u64
}

fn put_var_uint_64(out: &mut Vec<u8>, mut v: u64) {
    for _ in 0..MAX_VAR_LEN_64 {
        if v < 0x80 {
            break;
        }
        out.push(((v & 0xFF) | 0x80) as u8);
        v >>= 7;
    }
    out.push((v & 0xFF) as u8);
}

fn read_var_int_64(b: &[u8], pos: usize) -> Option<(i64, usize)> {
    let mut x: u64 = 0;
    let mut s: u32 = 0;
    for i in pos..pos + MAX_VAR_LEN_64 {
        let n = u64::from(*b.get(i)?);
        if n < 0x80 || i == pos + MAX_VAR_LEN_64 - 1 {
            let v = x | (n << s);
            return Some((((v >> 1) as i64) ^ -((v & 1) as i64), i + 1));
        }
        x |= (n & 0x7F) << s;
        s += 7;
    }
    None
}

fn encode_pathway(hash: u64, pathway_start_ms: i64, current_edge_start_ms: i64) -> Vec<u8> {
    let mut out = Vec::with_capacity(8 + 2 * (MAX_VAR_LEN_64 + 1));
    out.extend_from_slice(&hash.to_le_bytes());
    put_var_uint_64(&mut out, zigzag(pathway_start_ms));
    put_var_uint_64(&mut out, zigzag(current_edge_start_ms));
    out
}

fn decode_pathway(raw: &[u8]) -> Option<(u64, i64, i64)> {
    let hash = u64::from_le_bytes(raw.get(..8)?.try_into().ok()?);
    let (pathway_start_ms, pos) = read_var_int_64(raw, 8)?;
    let (current_edge_start_ms, _) = read_var_int_64(raw, pos)?;
    Some((hash, pathway_start_ms, current_edge_start_ms))
}

#[pyo3::pyfunction]
pub fn encode_pathway_b64(
    hash_value: u64,
    pathway_start_ms: i64,
    current_edge_start_ms: i64,
) -> String {
    BASE64.encode(encode_pathway(
        hash_value,
        pathway_start_ms,
        current_edge_start_ms,
    ))
}

/// Returns `None` whenever strict decoding fails, including input that Python's lenient
/// `base64.b64decode` would still accept; the caller then falls back to the Python decoder,
/// so results match it for every input.
#[pyo3::pyfunction]
pub fn decode_pathway_b64(data: &Bound<'_, PyAny>) -> Option<(u64, i64, i64)> {
    let raw = if let Ok(b) = data.cast::<PyBytes>() {
        BASE64.decode(b.as_bytes()).ok()?
    } else if let Ok(s) = data.cast::<PyString>() {
        BASE64.decode(s.to_str().ok()?).ok()?
    } else {
        return None;
    };
    decode_pathway(&raw)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trips() {
        // Values whose zigzag form fits in 9 varint bytes (|v| < 2^62). Beyond that the
        // encoder writes 10 bytes but decoding stops at 9, exactly like the Python codec.
        let big = (1i64 << 62) - 1;
        for (h, a, b) in [
            (0u64, 0i64, 0i64),
            (u64::MAX, big, -big),
            (0x1234_5678_9ABC_DEF0, 1_727_260_000_123, 1_727_260_000_456),
            (42, -1, -64),
        ] {
            let raw = encode_pathway(h, a, b);
            assert_eq!(decode_pathway(&raw), Some((h, a, b)));
        }
    }

    #[test]
    fn rejects_truncated_input() {
        let raw = encode_pathway(7, 1_727_260_000_123, 1_727_260_000_456);
        for cut in 0..raw.len() {
            assert_eq!(decode_pathway(&raw[..cut]), None, "cut at {cut}");
        }
    }
}

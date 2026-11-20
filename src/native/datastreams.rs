use base64::{engine::general_purpose::STANDARD, Engine as _};
use pyo3::{
    prelude::*,
    types::{PyBytes, PyString},
};

const FNV_64_PRIME: u64 = 0x100000001B3;
const FNV1_64_INIT: u64 = 0xCBF29CE484222325;

/// 64-bit FNV-1 hash of the given bytes.
#[pyfunction]
pub fn fnv1_64(data: &[u8]) -> u64 {
    data.iter().fold(FNV1_64_INIT, |hval, &byte| {
        hval.wrapping_mul(FNV_64_PRIME) ^ u64::from(byte)
    })
}

const MAX_VAR_LEN_64: usize = 9;
// 8 hash bytes plus two varints of up to MAX_VAR_LEN_64 + 1 bytes each.
const MAX_PATHWAY_LEN: usize = 8 + 2 * (MAX_VAR_LEN_64 + 1);
// 2**63 as f64: millisecond values at or beyond it do not fit in an i64.
const I64_LIMIT_F64: f64 = 9_223_372_036_854_775_808.0;

// Mirrors encoding.encode_var_uint_64, including its up to MAX_VAR_LEN_64 continuation bytes.
fn put_var_uint_64(buf: &mut [u8; MAX_PATHWAY_LEN], len: &mut usize, mut v: u64) {
    for _ in 0..MAX_VAR_LEN_64 {
        if v < 0x80 {
            break;
        }
        buf[*len] = (v as u8) | 0x80;
        *len += 1;
        v >>= 7;
    }
    buf[*len] = v as u8;
    *len += 1;
}

fn put_var_int_64(buf: &mut [u8; MAX_PATHWAY_LEN], len: &mut usize, v: i64) {
    put_var_uint_64(buf, len, ((v << 1) ^ (v >> 63)) as u64);
}

// Mirrors encoding.decode_var_uint_64: the last allowed byte is taken whole.
fn take_var_uint_64(data: &mut &[u8]) -> Option<u64> {
    let mut x: u64 = 0;
    for i in 0..MAX_VAR_LEN_64 {
        let n = *data.get(i)?;
        if n < 0x80 || i == MAX_VAR_LEN_64 - 1 {
            *data = &data[i + 1..];
            return Some(x | (u64::from(n) << (7 * i)));
        }
        x |= u64::from(n & 0x7F) << (7 * i);
    }
    None
}

fn take_var_int_64(data: &mut &[u8]) -> Option<i64> {
    let v = take_var_uint_64(data)?;
    Some(((v >> 1) as i64) ^ -((v & 1) as i64))
}

// Same truncation as Python's int(sec * 1e3); None when the result does not fit in an i64.
fn sec_to_ms(sec: f64) -> Option<i64> {
    let ms = sec * 1e3;
    if ms.is_finite() && (-I64_LIMIT_F64..I64_LIMIT_F64).contains(&ms) {
        Some(ms as i64)
    } else {
        None
    }
}

fn encode_pathway(hash: u64, pathway_start_ms: i64, edge_start_ms: i64) -> String {
    let mut buf = [0u8; MAX_PATHWAY_LEN];
    buf[..8].copy_from_slice(&hash.to_le_bytes());
    let mut len = 8;
    put_var_int_64(&mut buf, &mut len, pathway_start_ms);
    put_var_int_64(&mut buf, &mut len, edge_start_ms);
    STANDARD.encode(&buf[..len])
}

fn decode_pathway(encoded: &[u8]) -> Option<(u64, f64, f64)> {
    let decoded = STANDARD.decode(encoded).ok()?;
    let (hash_bytes, mut rest) = decoded.split_first_chunk::<8>()?;
    let pathway_start_ms = take_var_int_64(&mut rest)?;
    let edge_start_ms = take_var_int_64(&mut rest)?;
    Some((
        u64::from_le_bytes(*hash_bytes),
        pathway_start_ms as f64 / 1e3,
        edge_start_ms as f64 / 1e3,
    ))
}

/// Base64 pathway context, byte-identical to DataStreamsCtx.encode_b64.
/// Returns None for inputs the caller must encode in Python (non-u64 hash, out-of-range times).
#[pyfunction]
pub fn encode_pathway_b64(
    hash_value: &Bound<'_, PyAny>,
    pathway_start_sec: &Bound<'_, PyAny>,
    edge_start_sec: &Bound<'_, PyAny>,
) -> Option<String> {
    let hash = hash_value.extract::<u64>().ok()?;
    let pathway_start_ms = sec_to_ms(pathway_start_sec.extract::<f64>().ok()?)?;
    let edge_start_ms = sec_to_ms(edge_start_sec.extract::<f64>().ok()?)?;
    Some(encode_pathway(hash, pathway_start_ms, edge_start_ms))
}

/// Decodes a base64 pathway context into (hash, pathway_start_sec, edge_start_sec).
/// Returns None when the input is not strict, canonical base64 of a complete context, so
/// the caller can apply the lenient Python decoding and its error handling.
#[pyfunction]
pub fn decode_pathway_b64(data: &Bound<'_, PyAny>) -> Option<(u64, f64, f64)> {
    if let Ok(bytes) = data.cast::<PyBytes>() {
        decode_pathway(bytes.as_bytes())
    } else if let Ok(string) = data.cast::<PyString>() {
        decode_pathway(string.to_str().ok()?.as_bytes())
    } else {
        None
    }
}

pub fn register_datastreams(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(fnv1_64, m)?)?;
    m.add_function(wrap_pyfunction!(encode_pathway_b64, m)?)?;
    m.add_function(wrap_pyfunction!(decode_pathway_b64, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_fnv1_64_empty_is_offset_basis() {
        assert_eq!(fnv1_64(b""), FNV1_64_INIT);
    }

    #[test]
    fn test_fnv1_64_known_vectors() {
        // Reference values from the FNV test suite (http://isthe.com/chongo/tech/comp/fnv/).
        assert_eq!(fnv1_64(b"a"), 0xaf63bd4c8601b7be);
        assert_eq!(fnv1_64(b"foobar"), 0x340d8765a4dda9c2);
    }

    #[test]
    fn test_encode_pathway_known_value() {
        // 1679672748 encodes as the varint [216, 150, 238, 193, 12] (see tests/datastreams/test_encoding.py).
        let mut expected = 0x0102030405060708u64.to_le_bytes().to_vec();
        expected.extend_from_slice(&[216, 150, 238, 193, 12, 216, 150, 238, 193, 12]);
        assert_eq!(
            encode_pathway(0x0102030405060708, 1679672748, 1679672748),
            STANDARD.encode(expected)
        );
    }

    #[test]
    fn test_pathway_round_trip() {
        for (hash, start_ms, edge_ms) in [
            (0u64, 0i64, 0i64),
            // Larger magnitudes need a tenth varint byte, which the decoder (like Python's) rejects.
            (u64::MAX, (1 << 61) - 1, -(1 << 61)),
            (0xdeadbeef, 1_700_000_000_123, -1),
            (1, -1_700_000_000_123, 63),
        ] {
            let encoded = encode_pathway(hash, start_ms, edge_ms);
            assert_eq!(
                decode_pathway(encoded.as_bytes()),
                Some((hash, start_ms as f64 / 1e3, edge_ms as f64 / 1e3))
            );
        }
    }

    #[test]
    fn test_decode_pathway_rejects_invalid_input() {
        assert_eq!(decode_pathway(b""), None);
        assert_eq!(decode_pathway(b"not base64!"), None);
        // Valid base64 but too short for the hash.
        assert_eq!(decode_pathway(STANDARD.encode([1u8; 7]).as_bytes()), None);
        // Hash present but varints missing.
        assert_eq!(decode_pathway(STANDARD.encode([1u8; 8]).as_bytes()), None);
        // Unterminated varint.
        assert_eq!(
            decode_pathway(STANDARD.encode([1, 1, 1, 1, 1, 1, 1, 1, 0x80]).as_bytes()),
            None
        );
    }

    #[test]
    fn test_take_var_uint_64_takes_last_byte_whole() {
        let data = [0xFFu8; MAX_VAR_LEN_64 + 1];
        let mut rest = &data[..];
        let expected =
            (0..MAX_VAR_LEN_64 - 1).fold(0u64, |x, i| x | (0x7F << (7 * i))) | (0xFF << 56);
        assert_eq!(take_var_uint_64(&mut rest), Some(expected));
        assert_eq!(rest, &[0xFF]);
    }

    #[test]
    fn test_sec_to_ms_truncates_like_python_int() {
        assert_eq!(sec_to_ms(1.9999), Some(1999));
        assert_eq!(sec_to_ms(-1.9999), Some(-1999));
        assert_eq!(sec_to_ms(f64::NAN), None);
        assert_eq!(sec_to_ms(f64::INFINITY), None);
        assert_eq!(sec_to_ms(1e16), None);
    }
}

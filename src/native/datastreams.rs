use pyo3::prelude::*;

const FNV_64_PRIME: u64 = 0x100000001B3;
const FNV1_64_INIT: u64 = 0xCBF29CE484222325;

/// 64-bit FNV-1 hash of the given bytes.
#[pyfunction]
pub fn fnv1_64(data: &[u8]) -> u64 {
    data.iter().fold(FNV1_64_INIT, |hval, &byte| {
        hval.wrapping_mul(FNV_64_PRIME) ^ u64::from(byte)
    })
}

pub fn register_datastreams(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(fnv1_64, m)?)?;
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
}

use pyo3::prelude::*;
use std::collections::HashMap;

/// Formats the sum of two numbers as string.
#[pyfunction]
fn count_words(s: String) -> Py<PyAny> {
    let mut hm = HashMap::new();
    for sub_str in s.split(' ') {
        let count = hm
        .entry(sub_str)
        .or_insert(0);
    *count += 1;
    }
    
    return Python::with_gil(|py| {
        hm.to_object(py)
    });
    
}


#[pyfunction]
fn match_tags(tags: Vec<HashMap<String, i32>>) -> PyResult<bool> {
    let mut hm = HashMap::new();
    // for sub_str in s.split(' ') {
    //     let count = hm
    //     .entry(sub_str)
    //     .or_insert(0);
    // *count += 1;
    // }
    for tag in tags.iter() {
        tot += d[&key];
    }
    let is_match = r#match(subject, pattern);
    
    return Python::with_gil(|py| {
        is_match.to_object(py)
    });
    
}

pub fn r#match(subject: &str, pattern: &str) -> bool {
    let mut px: usize = 0;  // [p]attern inde[x]
    let mut sx: usize = 0;  // [s]ubject inde[x]
    let mut next_px: usize = 0;
    let mut next_sx: usize = 0;

    while px < pattern.len() || sx < subject.len() {
        if px < pattern.len() {
            let char = pattern.chars().nth(px).unwrap();

            if char == '?' {  // single character wildcard
                if sx < subject.len() {
                    px += 1;
                    sx += 1;
                    continue;
                }
            } else if char == '*' {  // zero-or-more-character wildcard
                next_px = px;
                next_sx = sx + 1;
                px += 1;
                continue;
            } else if sx < subject.len() && subject.chars().nth(sx).unwrap() == char {  // default normal character match
                px += 1;
                sx += 1;
                continue;
            }
        }

        if 0 < next_sx && next_sx <= subject.len() {
            px = next_px;
            sx = next_sx;
            continue;
        }

        return false;
    }
    true
}


// A Python module implemented in Rust.
#[pymodule]
fn word_counter(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(count_words, m)?)?;
    Ok(())
}
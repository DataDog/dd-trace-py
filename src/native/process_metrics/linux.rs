//! Linux process metrics, modelled on psutil._pslinux`.
//! Everything comes from `/proc` text files -- no `unsafe` beyond
//! `libc::sysconf` for the clock tick / page size constants.

use pyo3::exceptions::PyOSError;
use pyo3::prelude::*;
use std::fs;

fn io_err(context: &str, err: std::io::Error) -> PyErr {
    PyOSError::new_err(format!("{context}: {err}"))
}

/// Split `/proc/self/stat` into its whitespace-separated fields, skipping
/// past the `(comm)` field which can itself contain spaces and `)`
/// characters -- match psutil's `rfind(')')` approach rather than a naive
/// split, which would misalign every field after a comm like `a) (b`.
fn stat_fields(data: &str) -> Option<Vec<&str>> {
    let rpar = data.rfind(')')?;
    Some(data[rpar + 1..].split_whitespace().collect())
}

fn clock_ticks_per_sec() -> i64 {
    // SAFETY: _SC_CLK_TCK takes no arguments and never fails in a way that
    // corrupts memory; a negative return just means "unknown", handled below.
    let ticks = unsafe { libc::sysconf(libc::_SC_CLK_TCK) };
    if ticks > 0 {
        ticks
    } else {
        100 // historical default on Linux
    }
}

fn page_size_bytes() -> u64 {
    // SAFETY: same as above.
    let size = unsafe { libc::sysconf(libc::_SC_PAGESIZE) };
    if size > 0 {
        size as u64
    } else {
        4096
    }
}

pub fn process_metrics() -> PyResult<(u64, u64, i64, i64, u64, u64)> {
    let stat =
        fs::read_to_string("/proc/self/stat").map_err(|e| io_err("reading /proc/self/stat", e))?;
    let fields = stat_fields(&stat)
        .ok_or_else(|| PyOSError::new_err("unexpected /proc/self/stat format: missing ')'"))?;
    // Positions here are 0-indexed starting right after the `(comm)` field,
    // i.e. "man proc" position N corresponds to fields[N - 3].
    let field = |idx: usize| -> PyResult<u64> {
        fields
            .get(idx)
            .and_then(|s| s.parse::<u64>().ok())
            .ok_or_else(|| {
                PyOSError::new_err(format!(
                    "unexpected /proc/self/stat format: missing field {idx}"
                ))
            })
    };
    let utime_ticks = field(11)?;
    let stime_ticks = field(12)?;

    let ticks_per_sec = clock_ticks_per_sec() as u64;
    let cpu_time_user_ns = utime_ticks * 1_000_000_000 / ticks_per_sec;
    let cpu_time_sys_ns = stime_ticks * 1_000_000_000 / ticks_per_sec;

    let status = fs::read_to_string("/proc/self/status")
        .map_err(|e| io_err("reading /proc/self/status", e))?;
    let mut num_threads: Option<u64> = None;
    let mut voluntary: Option<i64> = None;
    let mut involuntary: Option<i64> = None;
    for line in status.lines() {
        if let Some(rest) = line.strip_prefix("Threads:") {
            num_threads = rest.trim().parse().ok();
        } else if let Some(rest) = line.strip_prefix("voluntary_ctxt_switches:") {
            voluntary = rest.trim().parse().ok();
        } else if let Some(rest) = line.strip_prefix("nonvoluntary_ctxt_switches:") {
            involuntary = rest.trim().parse().ok();
        }
    }
    let num_threads = num_threads
        .ok_or_else(|| PyOSError::new_err("'Threads' line not found in /proc/self/status"))?;
    // Older kernels (< 2.6.23) don't report ctxt switches; surface as "unavailable"
    // rather than fabricating a value.
    let ctx_switches_voluntary = voluntary.unwrap_or(-1);
    let ctx_switches_involuntary = involuntary.unwrap_or(-1);

    let statm = fs::read_to_string("/proc/self/statm")
        .map_err(|e| io_err("reading /proc/self/statm", e))?;
    let rss_pages: u64 = statm
        .split_whitespace()
        .nth(1)
        .and_then(|s| s.parse().ok())
        .ok_or_else(|| {
            PyOSError::new_err("unexpected /proc/self/statm format: missing rss field")
        })?;
    let rss_bytes = rss_pages * page_size_bytes();

    Ok((
        cpu_time_sys_ns,
        cpu_time_user_ns,
        ctx_switches_voluntary,
        ctx_switches_involuntary,
        num_threads,
        rss_bytes,
    ))
}

pub fn total_memory_bytes() -> PyResult<u64> {
    let meminfo =
        fs::read_to_string("/proc/meminfo").map_err(|e| io_err("reading /proc/meminfo", e))?;
    let mut mem_total_kb: Option<u64> = None;
    let mut swap_total_kb: Option<u64> = None;
    for line in meminfo.lines() {
        if let Some(rest) = line.strip_prefix("MemTotal:") {
            mem_total_kb = parse_kb_value(rest);
        } else if let Some(rest) = line.strip_prefix("SwapTotal:") {
            swap_total_kb = parse_kb_value(rest);
        }
    }
    let mem_total_kb = mem_total_kb
        .ok_or_else(|| PyOSError::new_err("'MemTotal' line not found in /proc/meminfo"))?;
    let swap_total_kb = swap_total_kb.unwrap_or(0);
    Ok((mem_total_kb + swap_total_kb) * 1024)
}

/// Parse a `/proc/meminfo` value like `  16332180 kB` into kibibytes.
fn parse_kb_value(rest: &str) -> Option<u64> {
    rest.split_whitespace().next()?.parse().ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_stat_fields_comm_with_parens_and_spaces() {
        // A process named "a) (b" would otherwise shift every subsequent field.
        let data = "1234 (a) (b) S 1 1234 1234 0 -1 4194560 100 0 0 0 5 3 0 0 20 0 4 0 100 0 0";
        let fields = stat_fields(data).unwrap();
        // Everything after the *last* ')' should parse cleanly regardless of
        // how many parens/spaces appear in the comm field.
        assert_eq!(fields[0], "S");
    }

    #[test]
    fn test_process_metrics_field_alignment() {
        // 39 fields after comm, matching /proc/[pid]/stat layout starting at
        // 'state' (man proc field 3). utime/stime are fields[11]/fields[12].
        let mut fields = vec!["S".to_string(), "1".to_string()];
        for i in 0..40 {
            fields.push(i.to_string());
        }
        let data = format!("1234 (comm) {}", fields.join(" "));
        let parsed = stat_fields(&data).unwrap();
        assert_eq!(parsed[11], "9"); // utime
        assert_eq!(parsed[12], "10"); // stime
    }

    #[test]
    fn test_parse_kb_value() {
        assert_eq!(parse_kb_value("   16332180 kB"), Some(16332180));
        assert_eq!(parse_kb_value("0 kB"), Some(0));
        assert_eq!(parse_kb_value("not a number"), None);
    }
}

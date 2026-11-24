//! Append-only table that maps span types to small integer ids.
//!
//! `SpanData` and `Context` store a span type id next to the span type string so that a reader
//! without the GIL (the stack profiler) never follows the `PyBackedString`, which a setter can
//! replace and free at any time. The reader copies the 4-byte id and turns it back into the full
//! string through this table.
//!
//! Entries are never removed or changed after they are published, and their bytes are never
//! freed. An entry is written first, then published with a release store of `count` and of its
//! index slot, so a reader that loads `count` (or a slot) with acquire ordering sees a complete
//! entry.
//!
//! Lookups of known types take no lock: they probe the open-addressing index with acquire loads.
//! Inserting takes `INSERT_LOCK`, which is held only for a few memory writes and never across a
//! call into Python. Every caller holds the GIL, so the lock is never held across `fork()` by
//! another thread on GIL builds.

use std::hash::Hasher as _;
use std::sync::atomic::{AtomicPtr, AtomicU32, Ordering};
use std::sync::Mutex;

/// Id for "no span type" (`None` or the empty string).
pub const NONE_ID: u32 = 0;
/// Id stored when the table is full. Readers drop the span type label for it.
pub const UNAVAILABLE_ID: u32 = u32::MAX;
/// Maximum number of entries, including the reserved entry 0.
pub const CAPACITY: usize = 1024;
/// Maximum total bytes of interned strings, so unbounded custom types cannot grow memory
/// without limit.
pub const MAX_TOTAL_BYTES: usize = 256 * 1024;

const INDEX_SLOTS: usize = CAPACITY * 2;
const INDEX_MASK: usize = INDEX_SLOTS - 1;

/// One published span type. Mirrored as `dd_span_type_entry_t` in `profiler_layout.h`.
///
/// `data` points to `len` bytes of UTF-8 followed by a NUL byte.
#[repr(C)]
pub struct Entry {
    pub data: AtomicPtr<u8>,
    pub len: AtomicU32,
    _reserved: u32,
}

impl Entry {
    const fn empty() -> Self {
        Self {
            data: AtomicPtr::new(std::ptr::null_mut()),
            len: AtomicU32::new(0),
            _reserved: 0,
        }
    }
}

static EMPTY_STRING: [u8; 1] = [0];

struct Table {
    /// Number of published entries. Entry 0 ("no type") is published lazily by `init`.
    count: AtomicU32,
    entries: [Entry; CAPACITY],
    /// Open-addressing index from string hash to entry id. 0 is an empty slot, because id 0 is
    /// never indexed.
    index: [AtomicU32; INDEX_SLOTS],
}

static TABLE: Table = Table {
    count: AtomicU32::new(0),
    entries: [const { Entry::empty() }; CAPACITY],
    index: [const { AtomicU32::new(0) }; INDEX_SLOTS],
};

/// Serializes inserts. Holds the total bytes of interned strings.
static INSERT_LOCK: Mutex<usize> = Mutex::new(0);

#[inline]
fn hash(s: &str) -> usize {
    let mut h = rustc_hash::FxHasher::default();
    h.write(s.as_bytes());
    h.finish() as usize
}

#[inline]
fn entry_matches(id: u32, s: &str) -> bool {
    let entry = &TABLE.entries[id as usize];
    // Acquire on the index slot (by the caller) makes these relaxed loads see the entry as
    // written before it was published.
    let len = entry.len.load(Ordering::Relaxed) as usize;
    if len != s.len() {
        return false;
    }
    let data = entry.data.load(Ordering::Relaxed);
    // SAFETY: published entries point to `len` immutable bytes that are never freed.
    unsafe { std::slice::from_raw_parts(data, len) == s.as_bytes() }
}

/// Probe the index. Returns the id when found, or the first empty slot otherwise.
#[inline]
fn probe(s: &str) -> Result<u32, usize> {
    let mut slot = hash(s) & INDEX_MASK;
    // The index is never more than half full, so an empty slot always ends the probe.
    loop {
        let id = TABLE.index[slot].load(Ordering::Acquire);
        if id == 0 {
            return Err(slot);
        }
        if entry_matches(id, s) {
            return Ok(id);
        }
        slot = (slot + 1) & INDEX_MASK;
    }
}

/// Publish entry 0, so `count` is at least 1 and every published id is a valid index.
pub fn init() {
    let _guard = INSERT_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    publish_none_entry();
}

/// Must be called with `INSERT_LOCK` held.
fn publish_none_entry() {
    if TABLE.count.load(Ordering::Relaxed) == 0 {
        let entry = &TABLE.entries[NONE_ID as usize];
        entry
            .data
            .store(EMPTY_STRING.as_ptr() as *mut u8, Ordering::Relaxed);
        entry.len.store(0, Ordering::Relaxed);
        TABLE.count.store(1, Ordering::Release);
    }
}

/// Return the id of `s`, adding it to the table on first use.
///
/// Returns `NONE_ID` for the empty string and `UNAVAILABLE_ID` when the table is full.
#[inline]
pub fn intern(s: &str) -> u32 {
    if s.is_empty() {
        return NONE_ID;
    }
    match probe(s) {
        Ok(id) => id,
        Err(_) => intern_slow(s),
    }
}

#[cold]
fn intern_slow(s: &str) -> u32 {
    let mut total_bytes = INSERT_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    // Inserts happen only under the lock, so this probe is authoritative.
    let slot = match probe(s) {
        Ok(id) => return id,
        Err(slot) => slot,
    };
    publish_none_entry();
    let id = TABLE.count.load(Ordering::Relaxed);
    if id as usize >= CAPACITY
        || s.len() > u32::MAX as usize
        || total_bytes.saturating_add(s.len()) > MAX_TOTAL_BYTES
    {
        return UNAVAILABLE_ID;
    }

    let mut bytes = Vec::with_capacity(s.len() + 1);
    bytes.extend_from_slice(s.as_bytes());
    bytes.push(0);
    // Leaked on purpose: readers may hold the pointer for the life of the process.
    let data = Box::leak(bytes.into_boxed_slice()).as_mut_ptr();

    let entry = &TABLE.entries[id as usize];
    entry.data.store(data, Ordering::Relaxed);
    entry.len.store(s.len() as u32, Ordering::Relaxed);
    *total_bytes += s.len();
    TABLE.count.store(id + 1, Ordering::Release);
    TABLE.index[slot].store(id, Ordering::Release);
    id
}

/// Return the string for `id`, or `None` when the id is not published or means "no type".
#[cfg_attr(not(test), allow(dead_code))]
pub fn lookup(id: u32) -> Option<&'static str> {
    if id == NONE_ID || id >= TABLE.count.load(Ordering::Acquire) {
        return None;
    }
    let entry = &TABLE.entries[id as usize];
    let len = entry.len.load(Ordering::Relaxed) as usize;
    let data = entry.data.load(Ordering::Relaxed);
    // SAFETY: published entries hold `len` bytes of valid UTF-8 that are never freed.
    Some(unsafe { std::str::from_utf8_unchecked(std::slice::from_raw_parts(data, len)) })
}

pub fn entries_ptr() -> *const Entry {
    TABLE.entries.as_ptr()
}

pub fn count_ptr() -> *const AtomicU32 {
    &TABLE.count
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_is_none() {
        assert_eq!(intern(""), NONE_ID);
        assert_eq!(lookup(NONE_ID), None);
    }

    #[test]
    fn round_trips_and_is_stable() {
        let web = intern("web");
        assert_ne!(web, NONE_ID);
        assert_ne!(web, UNAVAILABLE_ID);
        assert_eq!(intern("web"), web);
        assert_eq!(intern(&String::from("web")), web);
        assert_eq!(lookup(web), Some("web"));
        let sql = intern("sql");
        assert_ne!(sql, web);
        assert_eq!(lookup(sql), Some("sql"));
    }

    #[test]
    fn long_and_non_ascii() {
        let long = "x".repeat(4096);
        let id = intern(&long);
        assert_eq!(lookup(id), Some(long.as_str()));
        let s = "spän-type-ü-日本語-🙂";
        let id = intern(s);
        assert_eq!(lookup(id), Some(s));
        // Entries are NUL terminated for C readers.
        let entry = &TABLE.entries[id as usize];
        let data = entry.data.load(Ordering::Relaxed);
        let len = entry.len.load(Ordering::Relaxed) as usize;
        assert_eq!(unsafe { *data.add(len) }, 0);
    }

    #[test]
    fn unpublished_ids_do_not_resolve() {
        assert_eq!(lookup(UNAVAILABLE_ID), None);
        assert_eq!(lookup(CAPACITY as u32), None);
    }

    #[test]
    fn concurrent_interns_agree() {
        let names: Vec<String> = (0..64).map(|i| format!("concurrent-{i}")).collect();
        let results: Vec<Vec<u32>> = std::thread::scope(|scope| {
            let handles: Vec<_> = (0..8)
                .map(|_| scope.spawn(|| names.iter().map(|n| intern(n)).collect::<Vec<_>>()))
                .collect();
            handles.into_iter().map(|h| h.join().unwrap()).collect()
        });
        for r in &results[1..] {
            assert_eq!(r, &results[0]);
        }
        for (name, id) in names.iter().zip(&results[0]) {
            assert_eq!(lookup(*id), Some(name.as_str()));
        }
    }
}

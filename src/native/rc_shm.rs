//! SHM-backed remote-config file storage for the cross-process broadcast.
//!
//! Two anonymous, fork-inherited memfds, created lazily at the first fork
//! (`enable_shared_memory`); before that everything is kept in-process so a pure
//! single-process app pays no SHM cost.
//!
//! * **manifest**: a [`OneWayShmWriter`]/[`OneWayShmReader`] segment holding
//!   minimal fixed-sized data, including a generation seqlock guarding contents
//!   compaction.
//! * **contents**: a plain growable append-only SHM structure holding the configs.
//!   Each config is one `[path][content]` block. An update appends a fresh block
//!   and abandons the old. Existing bytes are never mutated in place - no lock needed.
//!   When dead bytes reach `2 × (live + initial size)`, the contents are compacted
//!   in place, guarded by increasing `compaction_seq`. When a reader detects a
//!   mismatched `compaction_seq`, it retries.

use std::cell::RefCell;
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use datadog_ipc::one_way_shared_memory::{create_anon_pair, OneWayShmReader, OneWayShmWriter};
use datadog_ipc::platform::{FileBackedHandle, MappedMem, ShmHandle};
use libdd_remote_config::fetch::FileStorage;
use libdd_remote_config::file_change_tracker::{FilePath, UpdatedFiles};
use libdd_remote_config::RemoteConfigPath;

use crate::remote_config::ChangeRecord;

/// Minimum size of the contents memfd; it grows on demand.
const INITIAL_CONTENTS_SIZE: usize = 0x1000;

/// A config's record in the manifest.
#[repr(C)]
#[derive(Clone, Copy)]
struct Entry {
    version: u64,
    /// offset to path in contents SHM
    offset: usize,
    /// length to path in contents SHM; offset + path_len = offset to content
    path_len: u32,
    /// length of content in contents SHM
    content_len: u32,
}

impl Entry {
    /// Total length of the `[path][content]` block in the arena.
    fn block_len(&self) -> usize {
        self.content_len as usize + self.path_len as usize
    }
}

#[repr(C)]
struct ManifestHeader {
    compaction_seq: u64,
    count: usize,
}

#[repr(C)]
struct Manifest {
    header: ManifestHeader,
    entries: [Entry],
}

impl Manifest {
    /// Reinterpret `bytes` as a `&Manifest`. Returns `None` when `bytes` is too
    /// short or misaligned.
    fn from_bytes(bytes: &[u8]) -> Option<&Manifest> {
        if bytes.len() < size_of::<ManifestHeader>()
            || (bytes.as_ptr() as usize) % align_of::<Entry>() != 0
        {
            return None;
        }
        // SAFETY: `bytes` is 8-aligned with at least a `ManifestHeader`.
        let count = unsafe { (bytes.as_ptr() as *const ManifestHeader).read() }.count;
        if bytes.len() < size_of::<ManifestHeader>() + count * size_of::<Entry>() {
            return None;
        }
        // count is needed to build the fat pointer
        // SAFETY: alignment + length checked above;
        let ptr = std::ptr::slice_from_raw_parts(bytes.as_ptr() as *const Entry, count)
            as *const Manifest;
        Some(unsafe { &*ptr })
    }
}

/// Serialize the manifest (the inverse of [`Manifest::from_bytes`]): the header
/// `[compaction_seq, count]` followed by the entries, each as raw struct bytes.
fn serialize_manifest(compaction_seq: u64, entries: &HashMap<String, Entry>) -> Vec<u8> {
    fn as_bytes<T>(value: &T) -> &[u8] {
        unsafe { std::slice::from_raw_parts(value as *const T as *const u8, size_of::<T>()) }
    }

    let mut buf =
        Vec::with_capacity(size_of::<ManifestHeader>() + entries.len() * size_of::<Entry>());
    buf.extend_from_slice(as_bytes(&ManifestHeader {
        compaction_seq,
        count: entries.len(),
    }));
    for entry in entries.values() {
        buf.extend_from_slice(as_bytes(entry));
    }
    buf
}

/// The origin's append-only view of the contents memfd.
struct ContentsArena {
    /// The origin's writable mapping. Readers get a handle to the same segment
    /// via [`MappedMem::into`] in [`ShmStorage::take_handles`].
    mapped: MappedMem<ShmHandle>,
    /// Bump pointer: offset of the next append (== live + free).
    used: usize,
    /// Sum of live (referenced) block lengths.
    live: usize,
    /// Sum of dead (abandoned) block lengths.
    free: usize,
}

impl ContentsArena {
    fn new() -> anyhow::Result<Self> {
        let mapped = ShmHandle::new(INITIAL_CONTENTS_SIZE)?.map()?;
        Ok(ContentsArena {
            mapped,
            used: 0,
            live: 0,
            free: 0,
        })
    }

    /// Append `bytes`, returning their offset. Grows the segment if needed.
    fn append(&mut self, bytes: &[u8]) -> usize {
        let off = self.used;
        let end = off + bytes.len();
        self.mapped.ensure_space(end.max(1));
        self.mapped.as_slice_mut()[off..end].copy_from_slice(bytes);
        self.used = end;
        self.live += bytes.len();
        off
    }

    fn read(&self, offset: usize, len: usize) -> Vec<u8> {
        self.mapped
            .as_slice()
            .get(offset..(offset + len))
            .map(|s| s.to_vec())
            .unwrap_or_default()
    }

    fn free(&mut self, len: usize) {
        self.live = self.live.saturating_sub(len);
        self.free += len;
    }

    fn should_compact(&self) -> bool {
        self.free > 0 && self.free >= 2 * (self.live.max(1) + INITIAL_CONTENTS_SIZE)
    }

    /// Overwrite the whole arena with `buf` (used by compaction).
    fn overwrite(&mut self, buf: &[u8]) {
        self.mapped.ensure_space(buf.len().max(1));
        self.mapped.as_slice_mut()[..buf.len()].copy_from_slice(buf);
        self.used = buf.len();
        self.live = buf.len();
        self.free = 0;
    }
}

struct ShmState {
    arena: ContentsArena,
    manifest_writer: OneWayShmWriter<ShmHandle>,
    entries: HashMap<String, Entry>,
    /// Even when stable; odd while a compaction is moving blocks in place.
    compaction_seq: u64,
}

impl ShmState {
    fn publish_manifest(&self) {
        let payload = serialize_manifest(self.compaction_seq, &self.entries);
        self.manifest_writer.write(&payload);
    }

    /// Compact the contents in place if dead bytes dominate.
    fn publish_and_maybe_compact(&mut self) {
        if self.arena.should_compact() {
            // Enter compaction: odd seq, republished so readers pause.
            self.compaction_seq += 1;
            self.publish_manifest();

            // Build the compacted buffer from the live blocks.
            let mut new_buf: Vec<u8> = Vec::with_capacity(self.arena.live);
            let mut new_offsets: Vec<(String, usize)> = Vec::with_capacity(self.entries.len());
            for (path, e) in self.entries.iter() {
                let off = new_buf.len();
                new_buf.extend_from_slice(&self.arena.read(e.offset, e.block_len()));
                new_offsets.push((path.clone(), off));
            }
            self.arena.overwrite(&new_buf);
            for (path, off) in new_offsets {
                if let Some(e) = self.entries.get_mut(&path) {
                    e.offset = off;
                }
            }

            // Leave compaction: even seq + new offsets.
            self.compaction_seq += 1;
        }
        self.publish_manifest();
    }
}

enum State {
    /// Single-process: contents kept in-process (path -> (version, bytes)).
    Local(HashMap<String, (u64, Vec<u8>)>),
    /// Broadcasting: contents in the memfds.
    Shm(ShmState),
}

/// Custom [`FileStorage`] that keeps config files in shared memory once
/// broadcasting (after the first fork). Owned by the fetcher; reached via
/// `SingleFetcher::file_storage`.
pub struct ShmStorage {
    state: RefCell<State>,
    /// Files updated this round, for the origin's [`ChangeTracker`]. The old
    /// content is unused by the ChangeRecord builder, so we pass an empty `Vec`.
    updated: RefCell<Vec<(Arc<ShmFile>, Vec<u8>)>>,
}

impl ShmStorage {
    pub fn new() -> Self {
        ShmStorage {
            state: RefCell::new(State::Local(HashMap::new())),
            updated: RefCell::new(Vec::new()),
        }
    }

    /// Enable the cross-process broadcast: create the two memfds and migrate the
    /// in-process files into them.
    /// Must be called before forking so children inherit the segments.
    pub fn enable_shared_memory(&self) -> anyhow::Result<()> {
        let mut state = self.state.borrow_mut();
        let local = match &mut *state {
            State::Local(map) => std::mem::take(map),
            State::Shm(_) => {
                return Ok(()); // Already broadcasting
            }
        };

        let (manifest_writer, _manifest_handle) = create_anon_pair()?;
        let mut arena = ContentsArena::new()?;
        let mut entries = HashMap::with_capacity(local.len());
        for (path, (version, content)) in local {
            // Store as [path][content]
            let offset = arena.append(path.as_bytes());
            arena.append(&content);
            let entry = Entry {
                version,
                offset,
                content_len: content.len() as u32,
                path_len: path.len() as u32,
            };
            entries.insert(path, entry);
        }
        let shm = ShmState {
            arena,
            manifest_writer,
            entries,
            compaction_seq: 0,
        };
        shm.publish_manifest();
        *state = State::Shm(shm);
        Ok(())
    }

    /// To use in forked children: grab the ShmHandles to move them to readers.
    pub fn take_handles(&self) -> Option<(ShmHandle, ShmHandle)> {
        let mut state = self.state.borrow_mut();
        match std::mem::replace(&mut *state, State::Local(HashMap::new())) {
            State::Shm(shm) => {
                let manifest = shm.manifest_writer.into_handle();
                let contents: ShmHandle = shm.arena.mapped.into();
                Some((manifest, contents))
            }
            State::Local(_) => None,
        }
    }

    /// The current bytes stored for `path` (read back from in-process or SHM).
    pub fn contents_for(&self, path: &str) -> Vec<u8> {
        match &*self.state.borrow() {
            State::Local(map) => map.get(path).map(|(_, b)| b.clone()).unwrap_or_default(),
            State::Shm(shm) => shm
                .entries
                .get(path)
                .map(|e| {
                    shm.arena
                        .read(e.offset + e.path_len as usize, e.content_len as usize)
                })
                .unwrap_or_default(),
        }
    }

    /// Drop a config from the storage.
    pub fn remove(&self, path: &str) {
        match &mut *self.state.borrow_mut() {
            State::Local(map) => {
                map.remove(path);
            }
            State::Shm(shm) => {
                if let Some(old) = shm.entries.remove(path) {
                    shm.arena.free(old.block_len());
                }
                shm.publish_and_maybe_compact();
            }
        }
    }

    fn store_bytes(&self, path: &str, version: u64, contents: &[u8]) {
        match &mut *self.state.borrow_mut() {
            State::Local(map) => {
                map.insert(path.to_string(), (version, contents.to_vec()));
            }
            State::Shm(shm) => {
                if let Some(old) = shm.entries.get(path) {
                    shm.arena.free(old.block_len());
                }
                // Store the block as [path][content] so the reader recovers the path.
                let offset = shm.arena.append(path.as_bytes());
                shm.arena.append(contents);
                shm.entries.insert(
                    path.to_string(),
                    Entry {
                        version,
                        offset,
                        content_len: contents.len() as u32,
                        path_len: path.len() as u32,
                    },
                );
                shm.publish_and_maybe_compact();
            }
        }
    }
}

impl Default for ShmStorage {
    fn default() -> Self {
        Self::new()
    }
}

/// A stored config file. Holds only its path and version; the bytes live in the storage.
pub struct ShmFile {
    path: Arc<RemoteConfigPath>,
    version: AtomicU64,
}

impl ShmFile {
    pub fn version(&self) -> u64 {
        self.version.load(Ordering::Relaxed)
    }
}

impl FilePath for ShmFile {
    fn path(&self) -> &RemoteConfigPath {
        &self.path
    }
}

impl FileStorage for ShmStorage {
    type StoredFile = ShmFile;

    fn store(
        &self,
        version: u64,
        path: Arc<RemoteConfigPath>,
        contents: Vec<u8>,
    ) -> anyhow::Result<Arc<Self::StoredFile>> {
        self.store_bytes(&path.to_string(), version, &contents);
        Ok(Arc::new(ShmFile {
            path,
            version: AtomicU64::new(version),
        }))
    }

    fn update(
        &self,
        file: &Arc<Self::StoredFile>,
        version: u64,
        contents: Vec<u8>,
    ) -> anyhow::Result<()> {
        self.store_bytes(&file.path.to_string(), version, &contents);
        file.version.store(version, Ordering::Relaxed);
        // Record the update for the ChangeTracker.
        self.updated.borrow_mut().push((file.clone(), Vec::new()));
        Ok(())
    }
}

impl UpdatedFiles<ShmFile, Vec<u8>> for ShmStorage {
    fn updated(&self) -> Vec<(Arc<ShmFile>, Vec<u8>)> {
        std::mem::take(&mut *self.updated.borrow_mut())
    }
}

/// Maximum content reading retries before giving up for this cycle (a racing writer
/// would otherwise make us spin). On give-up we return "no change"; the next
/// `wait_for_change` + read picks things up.
const MAX_CONTENT_READ_RETRIES: u32 = 16;

/// Consumer side of the broadcast: reads the manifest + contents from the
/// inherited memfds and diffs successive published states into change records
/// (removals first, matching the origin).
pub struct ShmReader {
    manifest: OneWayShmReader<ShmHandle, ()>,
    // `Option` for re-map (unmap -> adjust -> map), otherwise Some()
    contents: Option<MappedMem<ShmHandle>>,
    /// `(version, content)` tuples mapped by path of applied state
    active_configs: HashMap<String, (u64, Arc<Vec<u8>>)>,
}

impl ShmReader {
    pub fn new(manifest_handle: ShmHandle, contents_handle: ShmHandle) -> anyhow::Result<Self> {
        Ok(ShmReader {
            manifest: OneWayShmReader::new(manifest_handle.map()?, ()),
            contents: Some(contents_handle.map()?),
            active_configs: HashMap::new(),
        })
    }

    /// Block until the origin publishes a new manifest, or `timeout` elapses.
    pub fn wait_for_change(&mut self, timeout: std::time::Duration) -> bool {
        self.manifest.wait_for_change(timeout)
    }

    /// Read the latest published state and return the changes since the previous
    /// successful read (removals first).
    pub fn read(&mut self) -> Vec<ChangeRecord> {
        let (mut changed, mut bytes) = self.manifest.read();
        if !changed {
            return Vec::new();
        }

        // We retry to ensure the compaction_seq did not change
        for _ in 0..MAX_CONTENT_READ_RETRIES {
            let (compaction_seq, entries) = match Manifest::from_bytes(bytes) {
                Some(m) => (m.header.compaction_seq, m.entries.to_vec()),
                None => return Vec::new(),
            };

            // A compaction is moving blocks in place; offsets are unstable.
            // And we know that we'll get a change again soon.
            if compaction_seq & 1 == 1 {
                return Vec::new();
            }

            // Ensure the mapping is big enough
            let max_end = entries
                .iter()
                .map(|e| e.offset + e.block_len())
                .max()
                .unwrap_or(0);
            if max_end > self.contents.as_ref().map_or(0, |m| m.get_size()) {
                if let Some(mapped) = self.contents.take() {
                    let mut handle: ShmHandle = mapped.into();
                    let _ = handle.adjust_to_file_size();
                    self.contents = handle.map().ok();
                }
            }
            let contents = match self.contents.as_ref() {
                Some(c) => c.as_slice(),
                None => return Vec::new(), // shouldn't happen
            };

            // Build diff
            let mut adds_updates: Vec<ChangeRecord> = Vec::new();
            let mut new_configs: HashMap<String, (u64, Arc<Vec<u8>>)> =
                HashMap::with_capacity(entries.len());
            for e in entries {
                let start = e.offset;
                let path_end = start + e.path_len as usize;
                let content_end = path_end + e.content_len as usize;
                let (path_bytes, content_bytes) = match (
                    contents.get(start..path_end),
                    contents.get(path_end..content_end),
                ) {
                    (Some(p), Some(c)) => (p, c),
                    _ => {
                        return Vec::new();
                    }
                };
                let path = match std::str::from_utf8(path_bytes) {
                    Ok(s) => s,
                    Err(_) => continue,
                };
                // Unchanged if same path and version
                if let Some((v, c)) = self.active_configs.get(path) {
                    if *v == e.version {
                        new_configs.insert(path.to_string(), (e.version, c.clone()));
                        continue;
                    }
                }
                // Add or update: copy the bytes once and emit a record.
                let content = Arc::new(content_bytes.to_vec());
                new_configs.insert(path.to_string(), (e.version, content.clone()));
                if let Ok(parsed) = RemoteConfigPath::try_parse(path) {
                    let parsed: RemoteConfigPath = parsed.into();
                    adds_updates.push(ChangeRecord::new(
                        &parsed,
                        e.version,
                        Some(content.as_ref().clone()),
                    ));
                }
            }

            (changed, bytes) = self.manifest.read();
            if !changed {
                // Ensure we emit removals first
                let mut records: Vec<ChangeRecord> = Vec::new();
                for (path, (version, _)) in &self.active_configs {
                    if !new_configs.contains_key(path) {
                        if let Ok(parsed) = RemoteConfigPath::try_parse(path) {
                            let parsed: RemoteConfigPath = parsed.into();
                            records.push(ChangeRecord::new(&parsed, *version, None));
                        }
                    }
                }
                records.extend(adds_updates);
                self.active_configs = new_configs;
                return records;
            }
        }

        Vec::new() // Out of retries
    }
}

#[cfg(test)]
impl ShmStorage {
    /// Current compaction counter (even = stable). Test-only.
    fn compaction_seq(&self) -> u64 {
        match &*self.state.borrow() {
            State::Shm(shm) => shm.compaction_seq,
            State::Local(_) => 0,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use libdd_remote_config::fetch::FileStorage;
    use libdd_remote_config::RemoteConfigPath;
    use std::sync::Arc;

    fn rc_path(name: &str) -> Arc<RemoteConfigPath> {
        let s = format!("datadog/2/ASM_FEATURES/{name}/config");
        Arc::new(RemoteConfigPath::try_parse(&s).unwrap().into())
    }

    fn content_of<'a>(changes: &'a [ChangeRecord], path_name: &str) -> Option<&'a Vec<u8>> {
        changes
            .iter()
            .find(|c| c.path.contains(path_name))
            .and_then(|c| c.content.as_ref())
    }

    /// Copy `bytes` into an 8-aligned buffer so it can be cast as a `Manifest`
    /// (production reads come from the `u64`-backed one-way-shm reader, which is
    /// already aligned; a raw `Vec<u8>` may not be).
    fn aligned(bytes: &[u8]) -> Vec<u64> {
        let mut buf = vec![0u64; bytes.len().div_ceil(8)];
        // SAFETY: `buf` has room for `bytes.len()` bytes; both are valid for the copy.
        unsafe {
            std::ptr::copy_nonoverlapping(bytes.as_ptr(), buf.as_mut_ptr() as *mut u8, bytes.len());
        }
        buf
    }

    fn manifest_of(buf: &[u64], len: usize) -> &Manifest {
        // SAFETY: `buf` is `u64`-backed (8-aligned); `len` is the serialized length.
        Manifest::from_bytes(unsafe { std::slice::from_raw_parts(buf.as_ptr() as *const u8, len) })
            .expect("aligned manifest")
    }

    #[test]
    fn manifest_roundtrip() {
        let mut entries = HashMap::new();
        entries.insert(
            "a".to_string(),
            Entry {
                version: 7,
                offset: 16,
                content_len: 100,
                path_len: 1,
            },
        );
        entries.insert(
            "bb".to_string(),
            Entry {
                version: 9,
                offset: 200,
                content_len: 4096,
                path_len: 2,
            },
        );
        let bytes = serialize_manifest(42, &entries);
        let buf = aligned(&bytes);
        let m = manifest_of(&buf, bytes.len());
        assert_eq!(m.header.compaction_seq, 42);
        assert_eq!(m.header.count, 2);
        assert_eq!(m.entries.len(), 2);
        // Entries match the originals (order is HashMap iteration order, so match by
        // a unique field).
        for e in m.entries.iter() {
            let orig = entries.values().find(|o| o.offset == e.offset).unwrap();
            assert_eq!(e.version, orig.version);
            assert_eq!(e.content_len, orig.content_len);
            assert_eq!(e.path_len, orig.path_len);
        }
        // Empty manifest casts to zero entries; truncated buffer is rejected.
        let empty = serialize_manifest(0, &HashMap::new());
        let empty_buf = aligned(&empty);
        let m = manifest_of(&empty_buf, empty.len());
        assert_eq!(m.header.compaction_seq, 0);
        assert!(m.entries.is_empty());
        assert!(Manifest::from_bytes(&[0u8; 4]).is_none());
    }

    #[test]
    fn arena_append_free_compact_threshold() {
        let mut arena = ContentsArena::new().unwrap();
        let a = arena.append(b"aaaa");
        let b = arena.append(b"bbbbbb");
        assert_eq!(a, 0);
        assert_eq!(b, 4);
        assert_eq!(arena.read(a, 4), b"aaaa");
        assert_eq!(arena.read(b, 6), b"bbbbbb");
        assert_eq!(arena.live, 10);
        assert_eq!(arena.free, 0);
        assert!(!arena.should_compact());

        // Small dead bytes stay under the size-aware threshold.
        arena.free(4);
        assert_eq!(arena.live, 6);
        assert_eq!(arena.free, 4);
        assert!(!arena.should_compact());

        // A large dead block crosses 2 * (live + INITIAL_CONTENTS_SIZE).
        let big = vec![0u8; 8 * INITIAL_CONTENTS_SIZE];
        arena.append(&big);
        assert!(!arena.should_compact()); // the big block is still live
        arena.free(big.len());
        assert!(arena.should_compact());
    }

    #[test]
    fn growth_beyond_initial_segment() {
        // 5000 bytes > INITIAL_CONTENTS_SIZE forces the arena to grow and the
        // reader to remap to the larger file.
        let big = vec![0x5Au8; 5000];
        let storage = ShmStorage::new();
        storage.enable_shared_memory().unwrap();
        let _f = storage.store(1, rc_path("big"), big.clone()).unwrap();

        let (mh, ch) = storage.take_handles().unwrap();
        let mut reader = ShmReader::new(mh, ch).unwrap();
        let changes = reader.read();
        assert_eq!(content_of(&changes, "/big/"), Some(&big));
    }

    #[test]
    fn compaction_preserves_content() {
        // Churn a sizable config so dead bytes cross the threshold and the contents
        // are compacted in place; the reader must still read the latest.
        let storage = ShmStorage::new();
        storage.enable_shared_memory().unwrap();
        let f = storage.store(1, rc_path("c"), vec![1u8; 4000]).unwrap();
        for v in 2..=10u64 {
            storage.update(&f, v, vec![v as u8; 4000]).unwrap();
        }
        assert!(
            storage.compaction_seq() >= 2,
            "expected at least one compaction (seq={})",
            storage.compaction_seq()
        );

        let (mh, ch) = storage.take_handles().unwrap();
        let mut reader = ShmReader::new(mh, ch).unwrap();
        let changes = reader.read();
        assert_eq!(changes.len(), 1);
        assert_eq!(changes[0].version, 10);
        assert_eq!(content_of(&changes, "/c/"), Some(&vec![10u8; 4000]));
    }

    /// Write a `[path][content]` block at `offset` into the (shared) contents
    /// mapping and return its [`Entry`]. The reader maps the same memfd, so it
    /// observes these bytes.
    fn put_block(
        contents: &mut MappedMem<ShmHandle>,
        offset: usize,
        version: u64,
        path: &str,
        content: &[u8],
    ) -> Entry {
        let end = offset + path.len() + content.len();
        contents.ensure_space(end.max(1));
        let buf = contents.as_slice_mut();
        buf[offset..offset + path.len()].copy_from_slice(path.as_bytes());
        buf[offset + path.len()..end].copy_from_slice(content);
        Entry {
            version,
            offset,
            path_len: path.len() as u32,
            content_len: content.len() as u32,
        }
    }

    /// Exercises the reader's own diff (now hand-rolled against the memo, not the
    /// `ChangeTracker`): add, update, an identical republish (no records), and a
    /// removal — verifying removals carry no content and unchanged files are not
    /// re-emitted.
    #[test]
    fn reader_diffs_add_update_unchanged_remove() {
        // Manifest in a one-way-shm segment; contents in a plain segment shared
        // between a writable mapping and the reader's handle (same memfd, so the
        // reader sees writes made here).
        let (manifest_writer, manifest_reader) = create_anon_pair().unwrap();
        let contents_handle = ShmHandle::new(0x4000).unwrap();
        let mut contents = contents_handle.clone().map().unwrap();
        let mut reader = ShmReader::new(manifest_reader, contents_handle).unwrap();

        let a = "datadog/2/ASM_FEATURES/a/config";
        let b = "datadog/2/ASM_FEATURES/b/config";
        let mut used = 0usize;
        let mut entries: HashMap<String, Entry> = HashMap::new();

        // Round 1: add A@1.
        let e = put_block(&mut contents, used, 1, a, b"AAAA");
        used += e.block_len();
        entries.insert(a.to_string(), e);
        manifest_writer.write(&serialize_manifest(0, &entries));
        let changes = reader.read();
        assert_eq!(changes.len(), 1);
        assert_eq!(changes[0].version, 1);
        assert_eq!(content_of(&changes, "/a/"), Some(&b"AAAA".to_vec()));

        // Round 2: update A -> @2 (new bytes appended) and add B@1.
        let e_a = put_block(&mut contents, used, 2, a, b"AA22");
        used += e_a.block_len();
        let e_b = put_block(&mut contents, used, 1, b, b"BBBB");
        entries.insert(a.to_string(), e_a);
        entries.insert(b.to_string(), e_b);
        manifest_writer.write(&serialize_manifest(0, &entries));
        let changes = reader.read();
        assert_eq!(changes.len(), 2, "A updated + B added");
        assert_eq!(content_of(&changes, "/a/"), Some(&b"AA22".to_vec()));
        assert_eq!(content_of(&changes, "/b/"), Some(&b"BBBB".to_vec()));

        // Round 3: republish the identical manifest. Nothing changed, so even
        // though the generation bumps the reader emits no records.
        manifest_writer.write(&serialize_manifest(0, &entries));
        assert!(reader.read().is_empty(), "unchanged snapshot emits nothing");

        // Round 4: remove A (manifest keeps only B). A is emitted as a removal
        // with no content; unchanged B is not re-emitted.
        entries.remove(a);
        manifest_writer.write(&serialize_manifest(0, &entries));
        let changes = reader.read();
        assert_eq!(changes.len(), 1);
        assert!(changes[0].path.contains("/a/"));
        assert!(changes[0].content.is_none(), "removal carries no content");
    }
}

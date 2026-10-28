//! Checked-in C ABI declarations and lazy `libloading` loader for libddwaf 2.x.
//!
//! Building this module requires neither `libclang` nor a build-time `libddwaf` binary.
//! Layout invariants are verified at compile time against the pinned `ddwaf.h` header (2.1.0).

use libloading::Library;
use pyo3::exceptions::{PyOSError, PyRuntimeError};
use pyo3::PyErr;
use std::ffi::{c_char, c_int, c_void, CStr};
use std::mem::{offset_of, size_of};
use std::ptr;
use std::sync::{Arc, Mutex};

pub const ABI_MAJOR: u32 = 2;

pub const DDWAF_OBJ_INVALID: u8 = 0x00;
pub const DDWAF_OBJ_NULL: u8 = 0x01;
pub const DDWAF_OBJ_BOOL: u8 = 0x02;
pub const DDWAF_OBJ_SIGNED: u8 = 0x04;
pub const DDWAF_OBJ_UNSIGNED: u8 = 0x06;
pub const DDWAF_OBJ_FLOAT: u8 = 0x08;
pub const DDWAF_OBJ_STRING: u8 = 0x10;
pub const DDWAF_OBJ_LITERAL_STRING: u8 = 0x12;
pub const DDWAF_OBJ_SMALL_STRING: u8 = 0x14;
pub const DDWAF_OBJ_ARRAY: u8 = 0x20;
pub const DDWAF_OBJ_MAP: u8 = 0x40;
pub const DDWAF_OBJ_LARGE_ARRAY: u8 = 0xA0;
pub const DDWAF_OBJ_LARGE_MAP: u8 = 0xC0;

pub const DDWAF_SMALL_STRING_MAX: usize = 14;
pub const DDWAF_OBJ_MAX_CAPACITY: usize = 0xFFFF;

pub const DDWAF_ERR_INTERNAL: c_int = -3;
pub const DDWAF_ERR_INVALID_ARGUMENT: c_int = -1;

#[allow(non_camel_case_types)]
pub type ddwaf_handle = *mut c_void;
#[allow(non_camel_case_types)]
pub type ddwaf_context = *mut c_void;
#[allow(non_camel_case_types)]
pub type ddwaf_subcontext = *mut c_void;
#[allow(non_camel_case_types)]
pub type ddwaf_builder = *mut c_void;
#[allow(non_camel_case_types)]
pub type ddwaf_allocator = *mut c_void;

#[repr(C)]
#[derive(Copy, Clone)]
pub struct ddwaf_object_bool {
    pub type_: u8,
    pub val: bool,
}

#[repr(C)]
#[derive(Copy, Clone)]
pub struct ddwaf_object_signed {
    pub type_: u8,
    pub val: i64,
}

#[repr(C)]
#[derive(Copy, Clone)]
pub struct ddwaf_object_unsigned {
    pub type_: u8,
    pub val: u64,
}

#[repr(C)]
#[derive(Copy, Clone)]
pub struct ddwaf_object_float {
    pub type_: u8,
    pub val: f64,
}

#[repr(C)]
#[derive(Copy, Clone)]
pub struct ddwaf_object_string {
    pub type_: u8,
    pub size: u32,
    pub ptr: *mut c_char,
}

#[repr(C)]
#[derive(Copy, Clone)]
pub struct ddwaf_object_small_string {
    pub type_: u8,
    pub size: u8,
    pub data: [u8; DDWAF_SMALL_STRING_MAX],
}

#[repr(C)]
#[derive(Copy, Clone)]
pub struct ddwaf_object_array {
    pub type_: u8,
    pub size: u16,
    pub capacity: u16,
    pub ptr: *mut ddwaf_object,
}

#[repr(C)]
#[derive(Copy, Clone)]
pub struct ddwaf_object_map {
    pub type_: u8,
    pub size: u16,
    pub capacity: u16,
    pub ptr: *mut ddwaf_object_kv,
}

#[repr(C)]
#[derive(Copy, Clone)]
pub union ddwaf_object_via {
    pub b8: ddwaf_object_bool,
    pub i64_: ddwaf_object_signed,
    pub u64_: ddwaf_object_unsigned,
    pub f64_: ddwaf_object_float,
    pub str_: ddwaf_object_string,
    pub sstr: ddwaf_object_small_string,
    pub array: ddwaf_object_array,
    pub map: ddwaf_object_map,
}

#[repr(C)]
#[derive(Copy, Clone)]
#[allow(non_camel_case_types)]
pub union ddwaf_object {
    pub type_: u8,
    pub via: ddwaf_object_via,
}

#[repr(C)]
#[derive(Copy, Clone)]
#[allow(non_camel_case_types)]
pub struct ddwaf_object_kv {
    pub key: ddwaf_object,
    pub val: ddwaf_object,
}

const _: () = {
    assert!(size_of::<ddwaf_object>() == 16);
    assert!(size_of::<ddwaf_object_kv>() == 32);
    assert!(offset_of!(ddwaf_object_small_string, type_) == 0);
    assert!(offset_of!(ddwaf_object_small_string, size) == 1);
    assert!(offset_of!(ddwaf_object_small_string, data) == 2);
    assert!(offset_of!(ddwaf_object_string, type_) == 0);
    assert!(offset_of!(ddwaf_object_string, size) == 4);
    assert!(offset_of!(ddwaf_object_array, type_) == 0);
    assert!(offset_of!(ddwaf_object_array, size) == 2);
    assert!(offset_of!(ddwaf_object_array, capacity) == 4);
    assert!(offset_of!(ddwaf_object_map, type_) == 0);
    assert!(offset_of!(ddwaf_object_map, size) == 2);
    assert!(offset_of!(ddwaf_object_map, capacity) == 4);
    assert!(offset_of!(ddwaf_object_kv, key) == 0);
    assert!(offset_of!(ddwaf_object_kv, val) == 16);
};

impl ddwaf_object {
    #[inline(always)]
    pub const fn invalid() -> Self {
        Self {
            via: ddwaf_object_via {
                sstr: ddwaf_object_small_string {
                    type_: DDWAF_OBJ_INVALID,
                    size: 0,
                    data: [0; DDWAF_SMALL_STRING_MAX],
                },
            },
        }
    }

    #[inline(always)]
    pub const fn null() -> Self {
        Self {
            via: ddwaf_object_via {
                sstr: ddwaf_object_small_string {
                    type_: DDWAF_OBJ_NULL,
                    size: 0,
                    data: [0; DDWAF_SMALL_STRING_MAX],
                },
            },
        }
    }

    #[inline(always)]
    pub const fn bool_val(val: bool) -> Self {
        Self {
            via: ddwaf_object_via {
                b8: ddwaf_object_bool {
                    type_: DDWAF_OBJ_BOOL,
                    val,
                },
            },
        }
    }

    #[inline(always)]
    pub const fn signed_val(val: i64) -> Self {
        Self {
            via: ddwaf_object_via {
                i64_: ddwaf_object_signed {
                    type_: DDWAF_OBJ_SIGNED,
                    val,
                },
            },
        }
    }

    #[inline(always)]
    pub const fn float_val(val: f64) -> Self {
        Self {
            via: ddwaf_object_via {
                f64_: ddwaf_object_float {
                    type_: DDWAF_OBJ_FLOAT,
                    val,
                },
            },
        }
    }

    #[inline(always)]
    pub fn small_string(bytes: &[u8]) -> Self {
        debug_assert!(bytes.len() <= DDWAF_SMALL_STRING_MAX);
        let mut data = [0u8; DDWAF_SMALL_STRING_MAX];
        // SAFETY: `bytes.len() <= 14`, so `data` has enough room.
        unsafe {
            ptr::copy_nonoverlapping(bytes.as_ptr(), data.as_mut_ptr(), bytes.len());
        }
        Self {
            via: ddwaf_object_via {
                sstr: ddwaf_object_small_string {
                    type_: DDWAF_OBJ_SMALL_STRING,
                    size: bytes.len() as u8,
                    data,
                },
            },
        }
    }

    #[inline(always)]
    pub const fn heap_string(ptr: *mut c_char, size: u32) -> Self {
        Self {
            via: ddwaf_object_via {
                str_: ddwaf_object_string {
                    type_: DDWAF_OBJ_STRING,
                    size,
                    ptr,
                },
            },
        }
    }

    #[inline(always)]
    pub fn tag(&self) -> u8 {
        // SAFETY: `type_` is the common initial byte of every union variant.
        unsafe { self.type_ }
    }

    #[inline]
    pub unsafe fn as_bytes(&self) -> Option<&[u8]> {
        match self.tag() {
            DDWAF_OBJ_SMALL_STRING => {
                let sstr = unsafe { &self.via.sstr };
                let len = (sstr.size as usize).min(DDWAF_SMALL_STRING_MAX);
                Some(&sstr.data[..len])
            }
            DDWAF_OBJ_STRING | DDWAF_OBJ_LITERAL_STRING => {
                let str_ = unsafe { &self.via.str_ };
                if str_.ptr.is_null() || str_.size == 0 {
                    Some(&[])
                } else {
                    Some(unsafe {
                        std::slice::from_raw_parts(str_.ptr.cast::<u8>(), str_.size as usize)
                    })
                }
            }
            _ => None,
        }
    }

    #[inline]
    pub unsafe fn as_array_slice(&self, api: &Api) -> Option<&[ddwaf_object]> {
        match self.tag() {
            DDWAF_OBJ_ARRAY => {
                let arr = unsafe { &self.via.array };
                if arr.ptr.is_null() || arr.size == 0 {
                    Some(&[])
                } else {
                    Some(unsafe { std::slice::from_raw_parts(arr.ptr, arr.size as usize) })
                }
            }
            DDWAF_OBJ_LARGE_ARRAY => {
                let size = unsafe { (api.ddwaf_object_get_size)(self) };
                let ptr = unsafe { self.via.array.ptr };
                if ptr.is_null() || size == 0 {
                    Some(&[])
                } else {
                    Some(unsafe { std::slice::from_raw_parts(ptr, size) })
                }
            }
            _ => None,
        }
    }

    #[inline]
    pub unsafe fn as_map_slice(&self, api: &Api) -> Option<&[ddwaf_object_kv]> {
        match self.tag() {
            DDWAF_OBJ_MAP => {
                let map = unsafe { &self.via.map };
                if map.ptr.is_null() || map.size == 0 {
                    Some(&[])
                } else {
                    Some(unsafe { std::slice::from_raw_parts(map.ptr, map.size as usize) })
                }
            }
            DDWAF_OBJ_LARGE_MAP => {
                let size = unsafe { (api.ddwaf_object_get_size)(self) };
                let ptr = unsafe { self.via.map.ptr };
                if ptr.is_null() || size == 0 {
                    Some(&[])
                } else {
                    Some(unsafe { std::slice::from_raw_parts(ptr, size) })
                }
            }
            _ => None,
        }
    }
}

#[derive(Debug, Clone)]
pub enum LoadError {
    NoCandidates,
    SingleOpenFailed(String),
    AllCandidatesFailed(Vec<String>),
    MissingSymbol {
        candidate: String,
        symbol: &'static str,
        detail: String,
    },
    NullAllocator {
        candidate: String,
    },
    InvalidVersion {
        candidate: String,
        version: String,
        expected_major: u32,
    },
}

impl LoadError {
    pub fn into_py_err(self) -> PyErr {
        match self {
            LoadError::NoCandidates => PyOSError::new_err("could not load libddwaf (no candidate)"),
            LoadError::SingleOpenFailed(msg) => PyOSError::new_err(msg),
            LoadError::AllCandidatesFailed(failures) => {
                PyOSError::new_err(format!("could not load libddwaf ({})", failures.join(", ")))
            }
            LoadError::MissingSymbol {
                candidate,
                symbol,
                detail,
            } => PyOSError::new_err(format!(
                "libddwaf loaded from {candidate} is missing required symbol {symbol}: {detail}"
            )),
            LoadError::NullAllocator { candidate } => PyRuntimeError::new_err(format!(
                "libddwaf loaded from {candidate} returned a null default allocator"
            )),
            LoadError::InvalidVersion {
                candidate,
                version,
                expected_major,
            } => PyRuntimeError::new_err(format!(
                "libddwaf {version} loaded from {candidate} is not supported, ddtrace requires {expected_major}.x"
            )),
        }
    }
}

/// Complete, validated function table for a loaded `libddwaf` shared library.
///
/// `_lib` is declared last so that all function-pointer users and dependent owners
/// drop before the underlying shared object is closed.
pub struct Api {
    pub loaded_candidate: String,
    pub version: String,
    pub default_allocator: ddwaf_allocator,
    pub ddwaf_allocator_alloc:
        unsafe extern "C" fn(alloc: ddwaf_allocator, bytes: usize, alignment: usize) -> *mut c_void,
    pub ddwaf_object_destroy:
        unsafe extern "C" fn(object: *mut ddwaf_object, alloc: ddwaf_allocator),
    pub ddwaf_object_from_json: unsafe extern "C" fn(
        output: *mut ddwaf_object,
        json_str: *const c_char,
        length: u32,
        alloc: ddwaf_allocator,
    ) -> bool,
    pub ddwaf_object_set_array: unsafe extern "C" fn(
        object: *mut ddwaf_object,
        capacity: u16,
        alloc: ddwaf_allocator,
    ) -> *mut ddwaf_object,
    pub ddwaf_object_set_map: unsafe extern "C" fn(
        object: *mut ddwaf_object,
        capacity: u16,
        alloc: ddwaf_allocator,
    ) -> *mut ddwaf_object,
    pub ddwaf_object_get_size: unsafe extern "C" fn(object: *const ddwaf_object) -> usize,
    pub ddwaf_destroy: unsafe extern "C" fn(handle: ddwaf_handle),
    pub ddwaf_known_addresses:
        unsafe extern "C" fn(handle: ddwaf_handle, size: *mut u32) -> *const *const c_char,
    pub ddwaf_context_init:
        unsafe extern "C" fn(handle: ddwaf_handle, output_alloc: ddwaf_allocator) -> ddwaf_context,
    pub ddwaf_context_eval: unsafe extern "C" fn(
        context: ddwaf_context,
        data: *mut ddwaf_object,
        alloc: ddwaf_allocator,
        result: *mut ddwaf_object,
        timeout: u64,
    ) -> c_int,
    pub ddwaf_context_destroy: unsafe extern "C" fn(context: ddwaf_context),
    pub ddwaf_subcontext_init: unsafe extern "C" fn(context: ddwaf_context) -> ddwaf_subcontext,
    pub ddwaf_subcontext_eval: unsafe extern "C" fn(
        subcontext: ddwaf_subcontext,
        data: *mut ddwaf_object,
        alloc: ddwaf_allocator,
        result: *mut ddwaf_object,
        timeout: u64,
    ) -> c_int,
    pub ddwaf_subcontext_destroy: unsafe extern "C" fn(subcontext: ddwaf_subcontext),
    pub ddwaf_builder_init: unsafe extern "C" fn() -> ddwaf_builder,
    pub ddwaf_builder_add_or_update_config: unsafe extern "C" fn(
        builder: ddwaf_builder,
        path: *const c_char,
        path_len: u32,
        config: *const ddwaf_object,
        diagnostics: *mut ddwaf_object,
    ) -> bool,
    pub ddwaf_builder_remove_config:
        unsafe extern "C" fn(builder: ddwaf_builder, path: *const c_char, path_len: u32) -> bool,
    pub ddwaf_builder_build_instance: unsafe extern "C" fn(builder: ddwaf_builder) -> ddwaf_handle,
    pub ddwaf_builder_get_config_paths: unsafe extern "C" fn(
        builder: ddwaf_builder,
        paths: *mut ddwaf_object,
        filter: *const c_char,
        filter_len: u32,
    ) -> u32,
    pub ddwaf_builder_destroy: unsafe extern "C" fn(builder: ddwaf_builder),
    _lib: Library,
}

// SAFETY: `Api` holds an immutable loaded library, C function pointers, and the
// process-wide thread-safe default allocator pointer from `ddwaf_get_default_allocator`.
unsafe impl Send for Api {}
unsafe impl Sync for Api {}

static API: Mutex<Option<Arc<Api>>> = Mutex::new(None);

unsafe fn resolve_symbol<T: Copy>(
    lib: &Library,
    candidate: &str,
    name: &'static str,
    symbol_bytes: &[u8],
) -> Result<T, LoadError> {
    let sym = unsafe { lib.get::<T>(symbol_bytes) }.map_err(|err| LoadError::MissingSymbol {
        candidate: candidate.to_owned(),
        symbol: name,
        detail: err.to_string(),
    })?;
    let raw_ptr = unsafe { *(&*sym as *const T as *const *const c_void) };
    if raw_ptr.is_null() {
        return Err(LoadError::MissingSymbol {
            candidate: candidate.to_owned(),
            symbol: name,
            detail: "symbol resolved to NULL".to_owned(),
        });
    }
    Ok(*sym)
}

impl Api {
    pub fn load(candidates: &[String]) -> Result<Arc<Self>, LoadError> {
        if candidates.is_empty() {
            return Err(LoadError::NoCandidates);
        }

        let mut slot = API.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(api) = slot.as_ref() {
            return Ok(Arc::clone(api));
        }

        #[cfg(target_os = "linux")]
        {
            static LOAD_LIBRT: std::sync::Once = std::sync::Once::new();
            LOAD_LIBRT.call_once(|| unsafe {
                let _ = libc::dlopen(c"librt.so.1".as_ptr(), libc::RTLD_LAZY | libc::RTLD_GLOBAL);
            });
        }

        let mut open_failures = Vec::new();
        let mut opened: Option<(String, Library)> = None;
        for candidate in candidates {
            match unsafe { Library::new(candidate.as_str()) } {
                Ok(lib) => {
                    opened = Some((candidate.clone(), lib));
                    break;
                }
                Err(err) => {
                    if candidates.len() == 1 {
                        return Err(LoadError::SingleOpenFailed(err.to_string()));
                    }
                    open_failures.push(format!("{candidate}: {err}"));
                }
            }
        }

        let (loaded_candidate, lib) =
            opened.ok_or(LoadError::AllCandidatesFailed(open_failures))?;

        macro_rules! sym {
            ($name:ident, $ty:ty) => {
                unsafe {
                    resolve_symbol::<$ty>(
                        &lib,
                        &loaded_candidate,
                        stringify!($name),
                        concat!(stringify!($name), "\0").as_bytes(),
                    )?
                }
            };
        }

        let ddwaf_get_version = sym!(ddwaf_get_version, unsafe extern "C" fn() -> *const c_char);
        let ddwaf_get_default_allocator = sym!(
            ddwaf_get_default_allocator,
            unsafe extern "C" fn() -> ddwaf_allocator
        );
        let ddwaf_allocator_alloc = sym!(
            ddwaf_allocator_alloc,
            unsafe extern "C" fn(ddwaf_allocator, usize, usize) -> *mut c_void
        );
        let ddwaf_object_destroy = sym!(
            ddwaf_object_destroy,
            unsafe extern "C" fn(*mut ddwaf_object, ddwaf_allocator)
        );
        let ddwaf_object_from_json = sym!(
            ddwaf_object_from_json,
            unsafe extern "C" fn(*mut ddwaf_object, *const c_char, u32, ddwaf_allocator) -> bool
        );
        let ddwaf_object_set_array = sym!(
            ddwaf_object_set_array,
            unsafe extern "C" fn(*mut ddwaf_object, u16, ddwaf_allocator) -> *mut ddwaf_object
        );
        let ddwaf_object_set_map = sym!(
            ddwaf_object_set_map,
            unsafe extern "C" fn(*mut ddwaf_object, u16, ddwaf_allocator) -> *mut ddwaf_object
        );
        let ddwaf_object_get_size = sym!(
            ddwaf_object_get_size,
            unsafe extern "C" fn(*const ddwaf_object) -> usize
        );
        let ddwaf_destroy = sym!(ddwaf_destroy, unsafe extern "C" fn(ddwaf_handle));
        let ddwaf_known_addresses = sym!(
            ddwaf_known_addresses,
            unsafe extern "C" fn(ddwaf_handle, *mut u32) -> *const *const c_char
        );
        let ddwaf_context_init = sym!(
            ddwaf_context_init,
            unsafe extern "C" fn(ddwaf_handle, ddwaf_allocator) -> ddwaf_context
        );
        let ddwaf_context_eval = sym!(
            ddwaf_context_eval,
            unsafe extern "C" fn(
                ddwaf_context,
                *mut ddwaf_object,
                ddwaf_allocator,
                *mut ddwaf_object,
                u64,
            ) -> c_int
        );
        let ddwaf_context_destroy =
            sym!(ddwaf_context_destroy, unsafe extern "C" fn(ddwaf_context));
        let ddwaf_subcontext_init = sym!(
            ddwaf_subcontext_init,
            unsafe extern "C" fn(ddwaf_context) -> ddwaf_subcontext
        );
        let ddwaf_subcontext_eval = sym!(
            ddwaf_subcontext_eval,
            unsafe extern "C" fn(
                ddwaf_subcontext,
                *mut ddwaf_object,
                ddwaf_allocator,
                *mut ddwaf_object,
                u64,
            ) -> c_int
        );
        let ddwaf_subcontext_destroy = sym!(
            ddwaf_subcontext_destroy,
            unsafe extern "C" fn(ddwaf_subcontext)
        );
        let ddwaf_builder_init = sym!(ddwaf_builder_init, unsafe extern "C" fn() -> ddwaf_builder);
        let ddwaf_builder_add_or_update_config = sym!(
            ddwaf_builder_add_or_update_config,
            unsafe extern "C" fn(
                ddwaf_builder,
                *const c_char,
                u32,
                *const ddwaf_object,
                *mut ddwaf_object,
            ) -> bool
        );
        let ddwaf_builder_remove_config = sym!(
            ddwaf_builder_remove_config,
            unsafe extern "C" fn(ddwaf_builder, *const c_char, u32) -> bool
        );
        let ddwaf_builder_build_instance = sym!(
            ddwaf_builder_build_instance,
            unsafe extern "C" fn(ddwaf_builder) -> ddwaf_handle
        );
        let ddwaf_builder_get_config_paths = sym!(
            ddwaf_builder_get_config_paths,
            unsafe extern "C" fn(ddwaf_builder, *mut ddwaf_object, *const c_char, u32) -> u32
        );
        let ddwaf_builder_destroy =
            sym!(ddwaf_builder_destroy, unsafe extern "C" fn(ddwaf_builder));

        let version_ptr = unsafe { ddwaf_get_version() };
        if version_ptr.is_null() {
            return Err(LoadError::InvalidVersion {
                candidate: loaded_candidate,
                version: "<null>".to_owned(),
                expected_major: ABI_MAJOR,
            });
        }
        let version = unsafe { CStr::from_ptr(version_ptr) }
            .to_str()
            .unwrap_or("<invalid-utf8>")
            .to_owned();
        let expected_prefix = format!("{ABI_MAJOR}.");
        if !version.starts_with(&expected_prefix) {
            return Err(LoadError::InvalidVersion {
                candidate: loaded_candidate,
                version,
                expected_major: ABI_MAJOR,
            });
        }

        let default_allocator = unsafe { ddwaf_get_default_allocator() };
        if default_allocator.is_null() {
            return Err(LoadError::NullAllocator {
                candidate: loaded_candidate,
            });
        }

        let api = Arc::new(Self {
            loaded_candidate,
            version,
            default_allocator,
            ddwaf_allocator_alloc,
            ddwaf_object_destroy,
            ddwaf_object_from_json,
            ddwaf_object_set_array,
            ddwaf_object_set_map,
            ddwaf_object_get_size,
            ddwaf_destroy,
            ddwaf_known_addresses,
            ddwaf_context_init,
            ddwaf_context_eval,
            ddwaf_context_destroy,
            ddwaf_subcontext_init,
            ddwaf_subcontext_eval,
            ddwaf_subcontext_destroy,
            ddwaf_builder_init,
            ddwaf_builder_add_or_update_config,
            ddwaf_builder_remove_config,
            ddwaf_builder_build_instance,
            ddwaf_builder_get_config_paths,
            ddwaf_builder_destroy,
            _lib: lib,
        });

        *slot = Some(Arc::clone(&api));
        Ok(api)
    }

    /// The API loaded by `load`, if any. Loading is explicit so that registering the
    /// extension module never touches libddwaf.
    pub fn get() -> Option<Arc<Self>> {
        API.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }
}

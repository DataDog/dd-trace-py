// AIDEV-NOTE: This module provides Rust implementations for memory allocation profiling
// using the cxx bridge to expose functions to C++. This is the first step in migrating
// the memory profiler from C++ to Rust.

use std::collections::HashMap;

/// Maximum number of Traceback objects to keep in the pool
/// This prevents unbounded memory growth from the pool
const MAX_TRACEBACK_POOL_SIZE: usize = 1024;

/// HeapTracker manages heap allocation profiling state.
/// This will eventually replace the C++ heap tracker implementation.
pub struct HeapTracker {
    /// The sample size for heap profiling (in bytes)
    sample_size: u32,
    /// Map from allocation addresses to their C++ Traceback objects
    /// Key: void* (raw pointer to allocated memory)
    /// Value: UniquePtr to C++ Traceback (Rust owns the C++ objects via UniquePtr)
    allocations: HashMap<usize, cxx::UniquePtr<ffi::Traceback>>,
    /// Pool of reusable C++ Traceback objects to avoid repeated allocation/deallocation
    traceback_pool: Vec<cxx::UniquePtr<ffi::Traceback>>,
}

impl HeapTracker {
    /// Create a new HeapTracker with the specified sample size
    pub fn new(sample_size: u32) -> Self {
        println!("[Rust HeapTracker] Created with sample_size: {}", sample_size);
        let mut tracker = HeapTracker {
            sample_size,
            allocations: HashMap::new(),
            traceback_pool: Vec::new(),
        };
        
        // Test: Track some allocations with C++ Traceback objects
        tracker.track_allocation(0x1000, 5);  // Creates new Traceback
        tracker.track_allocation(0x2000, 10); // Creates new Traceback
        
        // Test: Print their descriptions (calls C++ print function)
        tracker.print_allocation_traceback(0x1000);
        tracker.print_allocation_traceback(0x2000);
        
        // Test: Untrack one (returns to pool)
        tracker.untrack_allocation(0x1000);
        
        // Test: Track another - should reuse from pool!
        tracker.track_allocation(0x3000, 15); // Should reuse pooled Traceback
        tracker.print_allocation_traceback(0x3000);
        
        tracker
    }
    
    /// Get the current sample size
    pub fn get_sample_size(&self) -> u32 {
        self.sample_size
    }
    
    /// Get a Traceback from the pool, or create a new one if the pool is empty
    fn get_traceback_from_pool(&mut self, initial_frame_count: usize) -> cxx::UniquePtr<ffi::Traceback> {
        if let Some(traceback) = self.traceback_pool.pop() {
            println!("[Rust HeapTracker] Reusing Traceback from pool (pool size: {})", self.traceback_pool.len());
            traceback
        } else {
            println!("[Rust HeapTracker] Creating new Traceback (pool empty)");
            unsafe { ffi::new_traceback(initial_frame_count) }
        }
    }
    
    /// Return a Traceback to the pool for reuse (if pool not full)
    fn return_traceback_to_pool(&mut self, traceback: cxx::UniquePtr<ffi::Traceback>) {
        if self.traceback_pool.len() < MAX_TRACEBACK_POOL_SIZE {
            println!("[Rust HeapTracker] Returning Traceback to pool (new pool size: {})", self.traceback_pool.len() + 1);
            self.traceback_pool.push(traceback);
        } else {
            println!("[Rust HeapTracker] Pool full ({}), discarding Traceback", MAX_TRACEBACK_POOL_SIZE);
            // traceback is dropped and the C++ object is destroyed
        }
    }
    
    /// Track an allocation by getting a C++ Traceback from the pool (or creating new)
    pub fn track_allocation(&mut self, ptr: usize, initial_frame_count: usize) {
        let traceback = self.get_traceback_from_pool(initial_frame_count);
        println!("[Rust HeapTracker] Tracking allocation at 0x{:x} with {} frames", 
                 ptr, traceback.size());
        self.allocations.insert(ptr, traceback);
    }
    
    /// Untrack an allocation and return its Traceback to the pool for reuse
    /// Returns true if the allocation was found and removed, false otherwise
    pub fn untrack_allocation(&mut self, ptr: usize) -> bool {
        if let Some(traceback) = self.allocations.remove(&ptr) {
            println!("[Rust HeapTracker] Untracked allocation at 0x{:x} ({} frames)", 
                     ptr, traceback.size());
            // Return the Traceback to the pool instead of destroying it
            self.return_traceback_to_pool(traceback);
            true
        } else {
            false
        }
    }
    
    /// Get the number of tracked allocations
    pub fn num_allocations(&self) -> usize {
        self.allocations.len()
    }
    
    /// Print the description of a tracked allocation's Traceback
    /// Returns true if the allocation was found, false otherwise
    pub fn print_allocation_traceback(&self, ptr: usize) -> bool {
        if let Some(traceback) = self.allocations.get(&ptr) {
            traceback.print_description();
            true
        } else {
            println!("[Rust HeapTracker] No traceback found for allocation at 0x{:x}", ptr);
            false
        }
    }
}

impl Drop for HeapTracker {
    fn drop(&mut self) {
        println!("[Rust HeapTracker] Destroyed (sample_size: {}, {} allocations tracked, {} in pool)", 
                 self.sample_size, self.allocations.len(), self.traceback_pool.len());
        // All C++ Traceback objects (both in allocations and pool) are automatically destroyed
    }
}

#[cxx::bridge(namespace = "ddtrace::profiling")]
mod ffi {
    // C++ types that Rust can use
    unsafe extern "C++" {
        include!("traceback.h");
        
        /// C++ Traceback type
        type Traceback;
        
        /// Increment frame count
        fn increment_frames(self: Pin<&mut Traceback>);
        
        /// Get the number of frames
        fn size(self: &Traceback) -> usize;
        
        /// Print the traceback description (calls C++ printf)
        fn print_description(self: &Traceback);
        
        /// Create a new C++ Traceback
        fn new_traceback(initial_count: usize) -> UniquePtr<Traceback>;
    }
    
    extern "Rust" {
        /// A trivial function to test the cxx bridge integration.
        /// Returns the input value multiplied by 2.
        fn rust_multiply_by_two(value: i32) -> i32;
        
        // HeapTracker type and methods
        type HeapTracker;
        
        /// Create a new HeapTracker and return it in a Box
        /// The Box allows C++ to own the Rust object via unique_ptr
        fn heap_tracker_new(sample_size: u32) -> Box<HeapTracker>;
        
        /// Get the sample size from a HeapTracker
        /// Using self: &HeapTracker makes this a method callable as tracker->get_sample_size()
        fn get_sample_size(self: &HeapTracker) -> u32;
    }
}

/// A trivial function to test the cxx bridge integration.
/// This will be called from C++ code in _memalloc.cpp.
fn rust_multiply_by_two(value: i32) -> i32 {
    value * 2
}

/// Create a new HeapTracker and return it in a Box.
/// This is a thin wrapper that allows cxx to call HeapTracker::new.
fn heap_tracker_new(sample_size: u32) -> Box<HeapTracker> {
    Box::new(HeapTracker::new(sample_size))
}

#[cfg(test)]
mod tests {
    use super::ffi::rust_multiply_by_two;

    #[test]
    fn test_multiply_by_two() {
        assert_eq!(rust_multiply_by_two(5), 10);
        assert_eq!(rust_multiply_by_two(0), 0);
        assert_eq!(rust_multiply_by_two(-3), -6);
    }
}

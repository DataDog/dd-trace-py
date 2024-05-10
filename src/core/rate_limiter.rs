use pyo3::prelude::*;
use std::cmp;
use std::sync::Mutex;

// Token bucket rate limiter
struct RateLimiter {
    rate_limit: i32,
    time_window: f64,
    tokens: i32,
    max_tokens: i32,
    last_update_ns: f64,
    current_window_ns: f64,
    tokens_allowed: i32,
    tokens_total: i32,
    prev_window_rate: Option<f64>,
    _lock: Mutex<()>,
}

impl RateLimiter {
    pub fn new(rate_limit: i32, time_window: f64) -> RateLimiter {
        RateLimiter {
            rate_limit,
            time_window,
            tokens: 0,
            max_tokens: rate_limit,
            last_update_ns: 0.0,
            current_window_ns: 0.0,
            tokens_allowed: 0,
            tokens_total: 0,
            prev_window_rate: None,
            _lock: Mutex::new(()),
        }
    }

    pub fn is_allowed(&mut self, timestamp_ns: f64) -> bool {
        let mut _lock = self._lock.lock().unwrap();

        let allowed = {
            if self.rate_limit == 0 {
                return false;
            } else if self.rate_limit < 0 {
                return true;
            }

            if self.tokens < self.max_tokens {
                let elapsed: f64 = (timestamp_ns - self.last_update_ns) / self.time_window;
                self.tokens = cmp::min(
                    self.max_tokens,
                    (self.tokens as f64 + (elapsed * self.max_tokens as f64)).ceil() as i32,
                );
            }

            // We have to ALWAYS update the last update time, but we need to do so after our calculations
            self.last_update_ns = timestamp_ns;

            if self.tokens >= 1 {
                self.tokens -= 1;
                return true;
            }

            false
        };

        // If we are in a new window, update the window rate
        if self.current_window_ns == 0.0 {
            self.current_window_ns = timestamp_ns;
        } else if timestamp_ns - self.current_window_ns >= self.time_window {
            self.prev_window_rate = Some(self.current_window_rate());
            self.current_window_ns = timestamp_ns;
            self.tokens_allowed = 0;
            self.tokens_total = 0;
        }

        // Update the token counts
        self.tokens_total += 1;
        if allowed {
            self.tokens_allowed += 1;
        }

        allowed
    }

    pub fn effective_rate(&self) -> f64 {
        let current_rate: f64 = self.current_window_rate();

        if self.prev_window_rate.is_none() {
            return current_rate;
        }

        current_rate + self.prev_window_rate.unwrap() / 2.0
    }

    fn current_window_rate(&self) -> f64 {
        // If no tokens have been seen then return 1.0
        // DEV: This is to avoid a division by zero error
        if self.tokens_total == 0 {
            return 1.0;
        }

        return self.tokens_allowed as f64 / self.tokens_total as f64;
    }
}

#[pyclass(name = "RateLimiter")]
pub struct RateLimiterPy {
    rate_limiter: RateLimiter,
}

#[pymethods]
impl RateLimiterPy {
    #[new]
    fn new(rate_limit: i32, time_window: f64) -> Self {
        RateLimiterPy {
            rate_limiter: RateLimiter::new(rate_limit, time_window),
        }
    }

    pub fn is_allowed(&mut self, py: Python<'_>, timestamp_ns: f64) -> bool {
        py.allow_threads(|| self.rate_limiter.is_allowed(timestamp_ns))
    }

    #[getter]
    pub fn effective_rate(&self) -> f64 {
        self.rate_limiter.effective_rate()
    }

    #[getter]
    pub fn rate_limit(&self) -> i32 {
        self.rate_limiter.rate_limit
    }
}

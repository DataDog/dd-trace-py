use pyo3::prelude::*;
use std::sync::Mutex;

// Token bucket rate limiter
struct RateLimiter {
    rate_limit: i32,
    time_window: f64,
    tokens: f64,
    max_tokens: f64,
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
            tokens: rate_limit as f64,
            max_tokens: rate_limit as f64,
            last_update_ns: 0.0,
            current_window_ns: 0.0,
            tokens_allowed: 0,
            tokens_total: 0,
            prev_window_rate: None,
            _lock: Mutex::new(()),
        }
    }

    pub fn _is_allowed(&mut self, timestamp_ns: f64) -> bool {
        let mut _lock = self._lock.lock().unwrap();

        let allowed = (|| -> bool {
            // Rate limit of 0 is always disallowed. Negative rate limits are always allowed.
            match self.rate_limit {
                0 => return false,
                _ if self.rate_limit < 0 => return true,
                _ => {}
            }

            if self.tokens < self.max_tokens {
                let mut elapsed: f64 = (timestamp_ns - self.last_update_ns) / self.time_window;
                if elapsed < 0.0 {
                    // Note - this should never happen, but if it does, we should reset the elapsed time to avoid negative tokens.
                    elapsed = 0.0
                }
                self.tokens += elapsed * self.max_tokens;
                if self.tokens > self.max_tokens {
                    self.tokens = self.max_tokens;
                }
            }

            self.last_update_ns = timestamp_ns;

            if self.tokens >= 1.0 {
                self.tokens -= 1.0;
                return true;
            }

            false
        })();

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

        (current_rate + self.prev_window_rate.unwrap()) / 2.0
    }

    fn current_window_rate(&self) -> f64 {
        // If no tokens have been seen then return 1.0
        // DEV: This is to avoid a division by zero error
        if self.tokens_total == 0 {
            return 1.0;
        }

        self.tokens_allowed as f64 / self.tokens_total as f64
    }
}

#[pyclass(name = "RateLimiter", subclass, module = "ddtrace.internal.core._core")]
pub struct RateLimiterPy {
    rate_limiter: RateLimiter,
}

#[pymethods]
impl RateLimiterPy {
    #[new]
    fn new(rate_limit: i32, time_window: Option<f64>) -> Self {
        RateLimiterPy {
            rate_limiter: RateLimiter::new(rate_limit, time_window.unwrap_or(1e9)),
        }
    }

    pub fn _is_allowed(&mut self, py: Python<'_>, timestamp_ns: f64) -> bool {
        py.allow_threads(|| self.rate_limiter._is_allowed(timestamp_ns))
    }

    #[getter]
    pub fn effective_rate(&self) -> f64 {
        self.rate_limiter.effective_rate()
    }

    #[getter]
    pub fn current_window_rate(&self) -> f64 {
        self.rate_limiter.current_window_rate()
    }

    #[getter]
    pub fn rate_limit(&self) -> i32 {
        self.rate_limiter.rate_limit
    }

    #[getter]
    pub fn time_window(&self) -> f64 {
        self.rate_limiter.time_window
    }

    #[getter]
    pub fn tokens(&self) -> f64 {
        self.rate_limiter.tokens
    }

    #[getter]
    pub fn max_tokens(&self) -> f64 {
        self.rate_limiter.max_tokens
    }

    #[getter]
    pub fn last_update_ns(&self) -> f64 {
        self.rate_limiter.last_update_ns
    }

    #[getter]
    pub fn current_window_ns(&self) -> f64 {
        self.rate_limiter.current_window_ns
    }

    #[getter]
    pub fn prev_window_rate(&self) -> Option<f64> {
        self.rate_limiter.prev_window_rate
    }

    #[getter]
    pub fn tokens_allowed(&self) -> i32 {
        self.rate_limiter.tokens_allowed
    }

    #[getter]
    pub fn tokens_total(&self) -> i32 {
        self.rate_limiter.tokens_total
    }
}

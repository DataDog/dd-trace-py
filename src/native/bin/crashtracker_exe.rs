use anyhow;
use datadog_crashtracker;

#[cfg(not(unix))]
fn main() {}

#[cfg(unix)]
fn main() -> anyhow::Result<()> {
    datadog_crashtracker::receiver_entry_point_stdin()
}

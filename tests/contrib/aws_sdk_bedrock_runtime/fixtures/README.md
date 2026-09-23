# Nova 2 Sonic protocol fixtures

Two live SDK 0.11.0 captures were sanitized before entering this repository.
Every transcript is synthetic, IDs are consistently remapped, timestamps are
relative, and audio payloads are replaced with byte counts. Tests reconstruct
silence, preserving sample counts and event order without storing speech.

Only successfully sent outbound events and observed inbound events remain.
The first capture used an application with a playback suppression bug; these
fixtures describe provider events, not that application's playback behavior.
The second covers multiple input windows per response, delayed final text,
queued output and four interruptions under a shared completion ID.

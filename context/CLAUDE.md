# Personal context for the remote Claude Code worker

This file is loaded by Claude Code on every headless (`claude -p`) invocation, on BOTH the
laptop worker and the cloud-fallback worker. Keep it in git so both stay in sync.

This is where your "personalization / memory" lives — the model is stateless, but this file
(plus the `memory/` directory) is re-fed as context every call, which is what makes the
assistant feel persistent and personalized.

## About me
- Name: Koushik
- Email: koushik.maji@recykal.com

## How to behave
- (Add standing instructions here: tone, what to assume, what to never do, etc.)

## Long-term memory
- Durable facts go in the `memory/` folder next to this file, one fact per file.
- Reference them when relevant.

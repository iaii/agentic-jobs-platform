from __future__ import annotations

# Shared across WriterAgent (system prompt "don't write these")
# and HiringManagerAgent (user message "flag any of these").
# One list = one source of truth. Update here; both agents pick it up.
BANNED_PHRASES: list[str] = [
    "I am drawn to",
    "I am excited to",
    "I am interested in",
    "Their work",
    "Their mission",
    "They are",
    "I look forward to discussing",
    "how my experiences align",
    "your company's goals",
    "I would love the opportunity",
    "I believe my skills make me a strong fit",
    "This demonstrates my ability to",
    "This work is similar to",
    "a natural fit for",
    "This experience will enable me to",
    "leveraging my expertise",
    "drive meaningful impact",
    "I want to be part of",
]

"""Processing pipeline: parse -> profile -> map -> validate -> resolve -> convert -> push.

Each stage is a self-contained module with the OSS library encapsulated inside, so a library
can be swapped without touching callers. See docs/ (plan §4).
"""

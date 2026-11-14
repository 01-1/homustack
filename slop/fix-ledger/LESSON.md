# Why this exists

In a two-week run with about 60 coding agents, requested fixes kept going
missing in two ways:

1. **Done meant "the artifact exists", not "the behaviour happens."** A rule
   was written, a checker passed, a task was triaged and checked off, and
   nothing ever ran the fix. The acceptance test measured the record, not the
   effect.
2. **Cleanup lost requests.** One request lived under an unrelated parent TODO
   with no owner. A separate triage item was checked off, and a later cleanup
   archived both, so nothing was left to show the request was still open.

The rule that came out of it: **a fix to a process is complete only when
something triggers it automatically and it has been seen working end to end.**
This tool enforces the mechanical part. It remembers every request it has seen,
restores the ones that disappear, and reopens "closed" process fixes that lack
an enabled trigger and an end-to-end receipt. Whether a receipt describes the
truth still needs a person or an independent reviewer.

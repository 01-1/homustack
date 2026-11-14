# Fix ledger

One line per fix someone asked for. An item leaves OPEN only with proof that the
fix works: a measured test or observation, not "triaged", "staged" or "reported".

Format:
- `- OPEN [fix:key] owner: <who> | asked: <date> "<quote>" | next: <step>`
- `- CLOSED [fix:key] owner: <who> | kind: behavior | proof: <what was measured, when>`
- `- CLOSED [fix:key] owner: <who> | kind: process | proof: <...> | receipt: <repo-relative JSON>`

Every OPEN item needs an unchecked TODO line that contains `[fix:key]`. If it
has none, the checker adds one to the first TODO file.

## Items

- OPEN [fix:example] owner: Ops | asked: 2030-01-01 "make X happen automatically" | next: add the timer

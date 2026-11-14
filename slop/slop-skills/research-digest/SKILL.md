---
name: research-digest
description: Gather all available public information on a topic (rules, a platform, competitors, earlier events, prior work) with research agents, archive the sources, and write a digest with a provenance label on every claim. Use when the human asks to find info or intel, to "look for ALL available info", or to answer a list of questions from sources.
---

# Research digest

1. Write the brief to `docs/research/briefs/<topic>-<date>.md` in the project first: the human's exact request with who and when, the question list, what is already known, limits and the output path.
2. Limits for every researcher: read-only, public or authorized sources only, no messages to people, no sign-ins, no external charges, no publishing, and a timebox. Ordinary subscription usage is not a cost concern. Respect any outside rules on what material may be used. Don't copy private material, credentials or someone else's workspace into the repo.
3. Archive first. When a source might disappear, save the relevant part under `docs/research/sources/` with its URL and the time you collected it, then read it.
4. Split the work by source or question across subagents. Use the models the human names; otherwise Claude Fable for judgement-heavy questions and Claude Opus or Sonnet for broad, careful sweeps.
5. The output is `docs/research/<topic>-<date>.md`. Label every claim with its source and one of: Official, Firsthand (who, where), Secondhand (who, where), Inferred. Where sources disagree, show both side by side.
6. For results you might imitate, such as winners or other teams, give placement, scores, sample size and confidence before any advice.
7. Cover every sub-question asked, and tick them off against the request before reporting.
8. Open each source that matters yourself, and mark the ones you couldn't open.
9. Never promote secondhand information to fact. It stays secondhand until an official source says the same.
10. Report the digest path, the three to five findings that change plans, and the conflicts and gaps. Facts that every agent needs go into AGENTS.md with the record-standing-rule skill.

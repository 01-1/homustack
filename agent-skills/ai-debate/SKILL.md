---
name: ai-debate
description: Have two or more debaters (fresh models, running agents, or a mix) answer one question on their own, debate each other's answers, and report what they agree on and what is still disputed. Use when the human says "ai debate", "debate it" or "have them debate", asks several named models for their own answers to the same question, or a role file says to debate using the ai-debate skill. For two parallel answers with no debate, use dual-agent (`../../slop/slop-skills/dual-agent`).
---

# AI debate

**Who.** The debaters are the models the human names, or the agents a role file names. With none named, use at least two fresh Claude Opus agents at high effort, none of which wrote the work being debated. Adding a different model (for example Claude Fable) is optional, when the human asks for it. Fresh models are spawned as subagents (in T3 Code, `delegate_task`); running agents are messaged in their own threads and keep their roles. Never substitute a model that isn't available; say so.

**Moderator.** Whoever runs the debate moderates: tallies, decides what is still disputed and writes the result. With no separate moderator, the debater the others send their answers to moderates, and also argues. A moderator who argues writes their own answer before reading anyone else's.

1. **Answer alone.** Each debater gets the same question and facts (with sources; unknowns labeled) and answers without seeing the others: the call first (or, for estimates and weights, the values with a one-line reason each), the deciding reasons with numbers, and what would change their mind. Debaters spawned for the debate only answer: no edits, no messages, no outward actions.
2. **Debate.** Each sees every other answer and replies: the strongest opposing point, the biggest flaw on each side, their final call or values, and whether they changed their mind and why. One round unless more are asked for; extra rounds cover only what is still disputed. Skip the debate if nothing material differs.
3. **Result.** The tally before and after; who changed their mind and why; what they agree on (plan or values); what is still disputed, with each side's best argument and value. If the moderator is also a debater, they decide each open dispute and say why, keeping it listed as disputed. Report to whoever asked, or write it where the role file says, and send it to the debaters; the debate ends there. For an important decision only the human can make, also raise it on the project's human channel.

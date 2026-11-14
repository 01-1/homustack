---
name: ping-owning-thread
description: Get the current state of work owned by other T3 threads by messaging them, not by reading their transcripts, and route a request to the right existing thread. Use when the human says "ask the agent", "ping threads", "send to that agent", "the other one", or when answering needs another thread's state.
---

# Ping the owning thread

Docs lag behind, because the human talks to owner threads directly. Ask the owner.

1. **Find the owner.** List open, non-settled threads (`t3_thread_list`). Settled threads count only when the human includes them. Match on title and on what the human said. "The existing one", "not a subagent" or "root level" means a top-level thread that already exists, not a new delegate.
2. **Not sure which thread?** Say so in one line with the 2–3 candidates and their links, or ask the human to check the T3 sidebar. Never send work to a thread that only looks likely.
3. **Status ping:** a short, read-only message, answered in the final reply only:
   > Read-only status question from <you>: what did you conclude, what is still open, and what may now be wrong? New facts: <facts that override>. Reply in ≤N lines.
4. **Work request:** a complete brief (goal, new facts, limits such as no purchases or sign-ins, report format), because the owner may be stale. New delegated reviews or re-checks use a new `delegate_task`, not `t3_thread_send`; status pings and coordinator messages are separate from that.
5. **Wait** for the reply notification. A minimal `t3_thread_read` is allowed to confirm which thread you mean before answering about it, and to retrieve its reply. Don't search its whole history instead of asking the owner; wider transcript reading needs the human's permission.
6. **Report** the owner's answer with its link, and mark anything you couldn't verify. If a ping fails or gets no answer, say which ones.

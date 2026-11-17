# slop

Rougher, less-tested extras.

| Path | What it is |
|---|---|
| `LESSONS.md` | 18 lessons from the multi-agent project, each with what to do instead. Reference only; agents don't need it. |
| `memories/` | Three example Claude Code memories and their `MEMORY.md` index. `memories/INDEX.md` describes each rule. |
| `slop-skills/` | Twelve more skills; see its README. |
| `fix-ledger/` | Keeps requested fixes from going missing; see its README. |

## Install the memories (optional)

Claude Code keeps memory per project in `~/.claude/projects/<slug>/memory/`, where the slug is the project's absolute path with every `/` replaced by `-` (for `/home/you/code/newproj`, `-home-you-code-newproj`). Run this from this folder (`slop/`):

```bash
M=~/.claude/projects/-home-you-code-newproj/memory
mkdir -p "$M"
cp -n memories/code-blocks-only-for-code.md memories/never-test-real-commands.md memories/ambiguous-short-message-no-action.md "$M"/
cat memories/MEMORY.md >> "$M"/MEMORY.md
```

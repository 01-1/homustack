---
name: code-blocks-only-for-code
description: "Code blocks only for code, commands or text that needs monospace; text the user reads goes as plain text"
metadata:
  type: feedback
---

Do not put text the user is meant to read in a fenced code block. Use a code block only for code, commands, or something that needs monospace. File paths the user wants to open go as clickable markdown links, not code blocks.

**Why:** the user found replies with too many code blocks hard to read, and asked for a path to be sent as a clickable file right after it was given in a code block.

**How to apply:** commands and text the user will paste somewhere still go in a code block; links, paths to read and short reply words like "ok" do not. Applies to every reply to the user.

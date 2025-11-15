# AGENTS

## Initial Setup for AI Assistants

When starting a new chat session, ALWAYS read and apply the rules from:
1. `.claude/CLAUDE.md` - Project skills and workflows
2. `.cursor/rules/*.mdc` - All rule files in this directory (version controlled):
   - `dd-trace-py.mdc` - Core project guidelines
   - `iast.mdc` - IAST/AppSec development guide
   - `linting.mdc` - Code quality and formatting
   - `testing.mdc` - Test execution guidelines
   - `repo-structure.mdc` - Repository structure

**Note:** `.cursor/rules/` is version controlled. `.cursor/context/` and `.cursor/tasks/` are gitignored.

Read these files at session start:
```bash
cat .claude/CLAUDE.md
cat .cursor/rules/dd-trace-py.mdc
cat .cursor/rules/iast.mdc
cat .cursor/rules/linting.mdc
cat .cursor/rules/testing.mdc
cat .cursor/rules/repo-structure.mdc
```

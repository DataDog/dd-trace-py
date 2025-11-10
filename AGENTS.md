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
## Dev environment tips

- Python interpreter: `/home/albertovara/.pyenv/versions/3.13.9/envs/ddtrace3.13/bin/python`
- Virtual environment: `pyenv activate ddtrace3.13`

## Testing instructions

- **ALWAYS use the `run-tests` skill** - never run pytest directly
- Use `--dry-run` first to see what tests would run
- See `.cursor/rules/testing.mdc` for full guidelines

## PR instructions

- Run `hatch run lint:checks` before committing
- Format all edited files with `hatch run lint:fmt -- <file>`
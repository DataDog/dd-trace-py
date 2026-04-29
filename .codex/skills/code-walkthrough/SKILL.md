---
name: code-walkthrough
description: Produces AI-assisted code walkthrough data for a specific function by analyzing its body and recursively following relevant nested function calls. Use when the user asks for /code-walkthrough, code walkthrough annotations, structured code locations, call graph explanation, or function behavior tracing.
disable-model-invocation: true
---

# Code Walkthrough

Use this skill to produce an AI-assisted walkthrough for a specific input function. The walkthrough must analyze the function body and recursively follow nested function calls that are relevant to understanding the behavior.

This skill is language-agnostic. It can be used for Python, JavaScript, TypeScript, Go, Java, Ruby, Rust, C, C++, C#, PHP, and other languages as long as the walkthrough producer can identify symbols, line ranges, and project-local call relationships.

## Inputs

Required:

- `entrypoint`: The function or method to analyze. Prefer a fully-qualified symbol when available, such as `package.module.Class.method` or `path/to/file.py:function_name`.
- `entrypoint`: The function or method to analyze. Prefer a fully-qualified symbol when available, such as `package.module.Class.method`, `pkg/module.py:function_name`, `src/service.ts:handleRequest`, or `src/foo/bar.go:ServeHTTP`.

Optional:

- `scope`: Files, directories, modules, or packages that should be considered in the walkthrough.
- `max_depth`: Maximum depth for recursively following function calls. Default to `3` when unspecified.
- `focus`: What the user wants to understand, such as error handling, data flow, side effects, performance, or instrumentation.
- `exclude`: Functions, modules, or call patterns to ignore, such as standard library helpers, obvious getters, logging-only helpers, or framework plumbing.

## Goal

Return an ordered sequence of code blocks with concise explanations of what each block does and why it matters to the walkthrough.

The output is intended for another tool, so do not return prose-only summaries. Always return valid JSON matching the schema below.

## Analysis Workflow

1. Locate the input function definition and identify its source file, line range, signature, parameters, return behavior, and immediate side effects.
2. Read the full function body before explaining it.
3. Build a call graph from the function body:
   - Include direct calls made by the entrypoint.
   - Recursively inspect project-local calls that materially affect behavior.
   - Follow calls through methods, decorators, wrappers, callbacks, helpers, trait methods, interface dispatch sites, or language-specific equivalents when they are important to the requested focus.
   - Stop recursion at `max_depth`, external library boundaries, trivial helpers, or code that does not change the explanation.
4. Identify the smallest useful set of contiguous code blocks needed to explain the behavior. Prefer important branches, state mutations, external interactions, error handling, data transformations, instrumentation, and return points.
5. For each selected block, write a concise explanation that explains the role of that code in the walkthrough. Avoid restating the code mechanically.
6. Preserve uncertainty explicitly. If a call target cannot be resolved, include it in `unresolved_calls` instead of guessing.
7. Write the final JSON object to `.cursor/code-walkthroughs/<entrypoint-slug>.json` and launch `scripts/view_code_walkthrough.py .cursor/code-walkthroughs/<entrypoint-slug>.json`. In this skill, that path resolves to the bundled viewer under `.codex/skills/code-walkthrough/scripts/`. Keep the server running in the background so the user can inspect the UI.

## Selection Criteria

Include a code block when it:

- Defines the entrypoint or a key nested function.
- Changes control flow in a meaningful way.
- Mutates state, configuration, request context, spans, metrics, or persistent data.
- Performs I/O, network calls, database calls, subprocess work, or external API calls.
- Handles errors, retries, fallbacks, or cleanup.
- Performs non-trivial data validation, normalization, transformation, or serialization.
- Explains why the function returns a specific value or raises a specific exception.

Exclude a code location when it is:

- A trivial getter, setter, constant lookup, or pass-through wrapper.
- Generic logging that does not affect behavior.
- Standard library or third-party implementation detail unless the user asked for it.
- Repetitive code already represented by a nearby block.

## Block Granularity

Prefer block-level explanation over line-level explanation. A block should usually be:

- A function signature plus its first setup statements.
- One branch arm, loop body, context-manager body, retry/fallback path, cleanup path, or return path.
- A compact run of related statements that performs one conceptual operation.
- A nested helper function body when following the call graph.

Do not create one block per line. Split a block only when the reader needs to understand a distinct decision, side effect, call, data transformation, or return behavior. When a block has several lines that are mechanically similar, explain the group once.

## Language Notes

- Use symbol names and call relationships that make sense for the target language and project conventions.
- `symbol`, `caller`, `callee`, and `called_from` do not need Python-style dotted names. Use the most stable readable identifier available for that language.
- `entrypoint`, `branch`, `call`, `return`, and the other `kind` values are semantic categories, not language-specific syntax categories.
- When the language uses multiple possible dispatch targets and the concrete target is uncertain, prefer `unresolved_calls` over guessing.
- When a language has file-local functions, methods, associated functions, trait or interface methods, closures, lambdas, anonymous callbacks, or macros, represent them using the clearest stable symbol you can recover.

## Block Ordering

Order `blocks` as a readable walkthrough, not as a flat file listing:

- Start with the entrypoint blocks in execution order.
- When a block calls a nested project-local function that is important to explain, put the nested function's blocks immediately after the calling block.
- After nested blocks are explained, return to the next relevant block in the caller.
- Use `depth` and `called_from` consistently so the viewer can show when the walkthrough enters a nested function or returns to the caller.
- Keep all blocks for the same conceptual branch together before moving to a different branch.

## Output Schema

Return exactly one JSON object with this shape:

```json
{
  "entrypoint": {
    "symbol": "service.Handler.handleRequest",
    "file": "src/service/handler.ts",
    "start_line": 10,
    "end_line": 42
  },
  "analysis": {
    "max_depth": 3,
    "focus": "data flow",
    "summary": "One concise sentence describing the behavior being walked through."
  },
  "blocks": [
    {
      "id": "block-001",
      "title": "Validate and normalize input",
      "file": "src/service/handler.ts",
      "start_line": 10,
      "end_line": 18,
      "symbol": "service.Handler.handleRequest",
      "depth": 0,
      "kind": "entrypoint",
      "importance": "high",
      "explanation": "Explains what this block does and why it matters.",
      "calls": ["service.RequestParser.parse"],
      "called_from": null
    }
  ],
  "call_graph": [
    {
      "caller": "service.Handler.handleRequest",
      "callee": "service.RequestParser.parse",
      "call_line": 15,
      "resolved": true,
      "depth": 1
    }
  ],
  "unresolved_calls": [
    {
      "caller": "service.Handler.handleRequest",
      "callee": "dynamic_callable",
      "call_line": 21,
      "reason": "Callable is loaded dynamically from runtime configuration."
    }
  ]
}
```

## Field Requirements

- `file` must be repository-relative when possible.
- `start_line` and `end_line` must be 1-indexed integers.
- `title` must be a short human-readable label for the block, not a copy of the code.
- `depth` must be `0` for the entrypoint, `1` for direct callees, and increase by one for each recursive call level.
- `kind` must be one of: `entrypoint`, `branch`, `call`, `state_change`, `io`, `error_handling`, `return`, `data_transform`, `configuration`, `instrumentation`, `cleanup`, `unknown`.
- `importance` must be one of: `high`, `medium`, `low`.
- `explanation` must be one to three complete sentences written for a human reader.
- `calls` must list resolved project-local calls made inside the block.
- `called_from` must be `null` for the entrypoint and otherwise the symbol that led to this location.
- `unresolved_calls` must be present even when empty.

## JSON Rules

- Save JSON only in the walkthrough file. Do not wrap the saved JSON in Markdown.
- Do not include code snippets unless a downstream tool explicitly requests them.
- Keep explanations concise and behavior-focused.
- Prefer fewer, higher-signal blocks over exhaustive line-by-line notes.
- Do not invent line numbers, symbols, or call relationships. If uncertain, report uncertainty in `unresolved_calls`.
- For backward compatibility only, the viewer can still display old `locations` JSON. New walkthroughs must use `blocks`.

## Viewer Handoff

Always open the interactive viewer after generating the walkthrough:

- Still produce exactly the same JSON object described above; the viewer depends on this schema.
- Save the JSON with stable two-space indentation under `.cursor/code-walkthroughs/`.
- Use a filename derived from the entrypoint symbol by replacing non-alphanumeric characters with `-`, lowercasing it, and trimming leading or trailing dashes.
- Start the viewer with:

```shell
scripts/view_code_walkthrough.py .cursor/code-walkthroughs/<entrypoint-slug>.json
```

- After the viewer starts, respond with the file path and local URL instead of printing the full JSON in chat.

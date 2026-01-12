# Prompt Registry Demo

This folder contains demo scripts for testing the LLMObs Prompt Registry end-to-end.

## Prerequisites

1. **RAPID Service** running in dd-source
2. **dd-trace-py** installed in editable mode
3. **DD_LLMOBS_PROMPTS_ENDPOINT** environment variable set

## Quick Start

### Terminal 1: Start RAPID Service

```bash
cd ~/DataDog/dd-source
git checkout alex/feat/llmobs-prompt-registry
rapid run --service llmobs-prompt-registry
```

Wait for: `INFO (rapid/rapid.go:558) - http api listening ... addr=127.0.0.1:8080`

### Terminal 2: Run Demo

```bash
cd ~/DataDog/dd-trace-py
git checkout alex/feat/get_prompt-demo
pip install -e .

export DD_LLMOBS_PROMPTS_ENDPOINT=http://localhost:8080
python demo-prompt-registry/demo_get_prompt.py
```

## Demo Scripts

### demo_get_prompt.py

Demonstrates:
1. **Text Template Prompt**: Fetch "greeting" prompt with `{{name}}` and `{{company}}` variables
2. **Chat Template Prompt**: Fetch "assistant" prompt with multi-message chat format
3. **Fallback Handling**: Graceful fallback when prompt not found
4. **Caching Behavior**: L1/L2 cache and stale-while-revalidate pattern

## Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `DD_LLMOBS_PROMPTS_ENDPOINT` | Override prompts API endpoint | `http://localhost:8080` |

## Branches

- **dd-source**: `alex/feat/llmobs-prompt-registry` - RAPID service
- **dd-trace-py**: `alex/feat/get_prompt-demo` - SDK with local endpoint support

## Troubleshooting

### Connection refused
Make sure RAPID service is running on port 8080.

### Import errors
Reinstall dd-trace-py: `pip install -e .`

### Wrong endpoint
Verify `DD_LLMOBS_PROMPTS_ENDPOINT` is set: `echo $DD_LLMOBS_PROMPTS_ENDPOINT`

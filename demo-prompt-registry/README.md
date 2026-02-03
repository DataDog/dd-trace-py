# Prompt Registry Demo

Demo scripts for testing the LLMObs Prompt Registry against staging.

## Prerequisites

1. **dd-auth** configured for staging (`dd.datad0g.com`)
2. **dd-trace-py** installed in editable mode
3. A `.venv` symlink or venv in this directory

## Setup

```bash
cd ~/DataDog/dd-trace-py
git checkout alex/feat/get_prompt-demo
pip install -e .

# Create venv symlink for demo scripts
ln -s ~/DataDog/dd-trace-py/.venv ~/DataDog/dd-trace-py/demo-prompt-registry/.venv
```

## Running Scripts

All scripts use `dd-auth` to authenticate against staging:

```bash
cd demo-prompt-registry

# Run any script
./run.sh scripts/01_basic_fetch.py
./run.sh scripts/02_chat_template.py
./run.sh scripts/04_caching_performance.py

# Or run the main demo
./run.sh demo_get_prompt.py
```

## Demo Scripts

| Script | Description |
|--------|-------------|
| `demo_get_prompt.py` | Full demo: text/chat templates, fallback, caching, refresh |
| `scripts/01_basic_fetch.py` | Basic prompt fetch from registry |
| `scripts/02_chat_template.py` | Chat template with multi-message format |
| `scripts/03a_warmup_cache.py` | Warm up cache before testing |
| `scripts/03b_network_failure.py` | Test fallback when network fails |
| `scripts/04_caching_performance.py` | Benchmark hot/warm cache performance |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DD_API_KEY` | (from dd-auth) | Datadog API key |
| `DD_LLMOBS_ML_APP` | varies by script | ML application name |
| `DD_LLMOBS_PROMPTS_ENDPOINT` | `api.datad0g.com` | Override for local dev |

## Troubleshooting

### Authentication errors
Make sure dd-auth is working: `dd-auth --domain dd.datad0g.com env | grep DD_API_KEY`

### Import errors
Reinstall dd-trace-py: `pip install -e .`

### Prompt not found
Check that the prompt exists in the staging Prompt Registry for the specified `ml_app`.

```
ORGSTORE_DISABLE_VERSION_CHECK=1 orgstore toolbox psql --datacenter us1.staging.dog --orgstore-cluster llm-obs -e "SELECT prompt_id, ml_app, version, user_version, labels, template FROM prompt_versions WHERE ml_app = '<ml_app>' AND prompt_id = '<prompt_id>' LIMIT 20;"
```
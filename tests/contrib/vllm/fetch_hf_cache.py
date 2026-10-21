"""Regenerate the Hugging Face cache used by the vLLM tests.

The tests load models with ``load_format="dummy"`` (random weights), so vLLM only needs the model config and
tokenizer files. This script writes those files into ``hf_cache/`` with the Hugging Face cache layout. The
tests set ``HF_HUB_CACHE`` to that directory and ``HF_HUB_OFFLINE=1``, so CI never calls the Hugging Face Hub.

Usage: python tests/contrib/vllm/fetch_hf_cache.py
"""

from pathlib import Path
import shutil
import urllib.request


HF_CACHE_DIR = Path(__file__).parent / "hf_cache"

# repo id -> (revision, files). Keep only config and tokenizer files: no weights.
MODELS = {
    "facebook/opt-125m": (
        "27dcfa74d334bc871f3234de431e71c6eeba5dd6",
        [
            "config.json",
            "generation_config.json",
            "merges.txt",
            "special_tokens_map.json",
            "tokenizer_config.json",
            "vocab.json",
        ],
    ),
    "intfloat/e5-small": (
        "e272f3049e853b47cb5ca3952268c6662abda68f",
        [
            "1_Pooling/config.json",
            "config.json",
            "modules.json",
            "sentence_bert_config.json",
            "special_tokens_map.json",
            "tokenizer.json",
            "tokenizer_config.json",
            "vocab.txt",
        ],
    ),
    "intfloat/e5-small-v2": (
        "ffb93f3bd4047442299a41ebb6fa998a38507c52",
        [
            "1_Pooling/config.json",
            "config.json",
            "modules.json",
            "sentence_bert_config.json",
            "special_tokens_map.json",
            "tokenizer.json",
            "tokenizer_config.json",
            "vocab.txt",
        ],
    ),
    "BAAI/bge-reranker-v2-m3": (
        "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
        [
            "config.json",
            # The fast tokenizer is built from this file. tokenizer.json (17MB) is not kept to limit the repo size.
            "sentencepiece.bpe.model",
            "special_tokens_map.json",
            "tokenizer_config.json",
        ],
    ),
}


def main() -> None:
    shutil.rmtree(HF_CACHE_DIR, ignore_errors=True)
    for repo_id, (revision, files) in MODELS.items():
        repo_dir = HF_CACHE_DIR / ("models--" + repo_id.replace("/", "--"))
        (repo_dir / "refs").mkdir(parents=True)
        (repo_dir / "refs" / "main").write_text(revision)
        for filename in files:
            target = repo_dir / "snapshots" / revision / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            url = f"https://huggingface.co/{repo_id}/resolve/{revision}/{filename}"
            with urllib.request.urlopen(url) as response:  # nosec B310
                target.write_bytes(response.read())
            print(f"{repo_id}: {filename}")


if __name__ == "__main__":
    main()

"""Tiny models for the vLLM tests.

The tests load models with load_format="dummy" (random weights), so vLLM reads only the model config and the
tokenizer. This module writes tiny configs and a byte-level tokenizer into local model directories. The tests
pass these directories to vLLM as the model, so vLLM never calls the Hugging Face Hub, and the repository
stores no model files.

The configs keep the architectures of real models (OPT, BERT, XLM-RoBERTa), so vLLM runs the same code paths.
"""

import json
from pathlib import Path
import tempfile


# vLLM reports the model directory as the model name.
MODELS_DIR = Path(tempfile.mkdtemp(prefix="dd-trace-py-vllm-models-"))
TINY_OPT = str(MODELS_DIR / "tiny-opt")
TINY_BERT = str(MODELS_DIR / "tiny-bert")
TINY_RERANKER = str(MODELS_DIR / "tiny-xlm-roberta-reranker")

HIDDEN_SIZE = 64

# The special tokens use the XLM-RoBERTa ids, which OPT also uses for <pad> (1) and </s> (2).
_SPECIAL_TOKENS = ["<s>", "<pad>", "</s>", "<unk>", "<mask>"]


def _byte_tokens():
    """Return the 256 byte-level tokens (the GPT-2 byte-to-unicode mapping)."""
    printable = list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1)) + list(range(ord("®"), 256))
    chars = {}
    extra = 0
    for b in range(256):
        if b in printable:
            chars[b] = chr(b)
        else:
            chars[b] = chr(256 + extra)
            extra += 1
    return [chars[b] for b in range(256)]


# One vocabulary for all models: the special tokens, then one token for each byte. There are no merges, so
# each byte of the input is one token, and decoding gives back the input text. The integration decodes the
# prompt token ids to text on some vLLM versions.
_VOCAB = {token: i for i, token in enumerate(_SPECIAL_TOKENS + _byte_tokens())}


def _token(content, special=True):
    return {
        "id": _VOCAB[content],
        "content": content,
        "single_word": False,
        "lstrip": False,
        "rstrip": False,
        "normalized": False,
        "special": special,
    }


def _template(pieces):
    """Convert a template such as "<s> $A </s>" to the TemplateProcessing format."""
    result = []
    for piece in pieces.split():
        if piece.startswith("$"):
            result.append({"Sequence": {"id": piece[1:], "type_id": 0}})
        else:
            result.append({"SpecialToken": {"id": piece, "type_id": 0}})
    return result


def _tokenizer_json(single, pair):
    special_tokens = sorted({p for p in (single + " " + pair).split() if not p.startswith("$")})
    return {
        "version": "1.0",
        "truncation": None,
        "padding": None,
        "added_tokens": [_token(t) for t in _SPECIAL_TOKENS],
        "normalizer": None,
        "pre_tokenizer": {"type": "ByteLevel", "add_prefix_space": False, "trim_offsets": True, "use_regex": True},
        "post_processor": {
            "type": "TemplateProcessing",
            "single": _template(single),
            "pair": _template(pair),
            "special_tokens": {t: {"id": t, "ids": [_VOCAB[t]], "tokens": [t]} for t in special_tokens},
        },
        "decoder": {"type": "ByteLevel", "add_prefix_space": False, "trim_offsets": True, "use_regex": True},
        "model": {
            "type": "BPE",
            "dropout": None,
            "unk_token": None,
            "continuing_subword_prefix": None,
            "end_of_word_suffix": None,
            "fuse_unk": False,
            "byte_fallback": False,
            "ignore_merges": False,
            "vocab": _VOCAB,
            "merges": [],
        },
    }


_TOKENIZER_CONFIG = {
    "tokenizer_class": "PreTrainedTokenizerFast",
    "bos_token": "<s>",
    "eos_token": "</s>",
    "unk_token": "<unk>",
    "pad_token": "<pad>",
    "cls_token": "<s>",
    "sep_token": "</s>",
    "mask_token": "<mask>",
    "model_max_length": 1024,
}

# Values common to the encoder configs.
_ENCODER = {
    "hidden_act": "gelu",
    "hidden_size": HIDDEN_SIZE,
    "intermediate_size": 4 * HIDDEN_SIZE,
    "num_attention_heads": 2,
    "num_hidden_layers": 2,
    "layer_norm_eps": 1e-12,
    "pad_token_id": 1,
    "position_embedding_type": "absolute",
    "torch_dtype": "float32",
    "vocab_size": len(_VOCAB),
}

MODELS = {
    TINY_OPT: {
        "config.json": {
            "architectures": ["OPTForCausalLM"],
            "model_type": "opt",
            "activation_function": "relu",
            "bos_token_id": 2,
            "eos_token_id": 2,
            "pad_token_id": 1,
            "do_layer_norm_before": True,
            "ffn_dim": 4 * HIDDEN_SIZE,
            "hidden_size": HIDDEN_SIZE,
            "word_embed_proj_dim": HIDDEN_SIZE,
            "max_position_embeddings": 1024,
            "num_attention_heads": 2,
            "num_hidden_layers": 2,
            "torch_dtype": "float16",
            "vocab_size": len(_VOCAB),
        },
        # Like OPT, prepend </s> as the BOS token.
        "tokenizer.json": _tokenizer_json(single="</s> $A", pair="</s> $A $B"),
        "tokenizer_config.json": dict(_TOKENIZER_CONFIG, bos_token="</s>"),
    },
    TINY_BERT: {
        "config.json": dict(
            _ENCODER,
            architectures=["BertModel"],
            model_type="bert",
            max_position_embeddings=512,
            type_vocab_size=2,
        ),
        "tokenizer.json": _tokenizer_json(single="<s> $A </s>", pair="<s> $A </s> $B </s>"),
        "tokenizer_config.json": _TOKENIZER_CONFIG,
        # sentence-transformers files: vLLM reads the pooling type from them.
        "modules.json": [
            {"idx": 0, "name": "0", "path": "", "type": "sentence_transformers.models.Transformer"},
            {"idx": 1, "name": "1", "path": "1_Pooling", "type": "sentence_transformers.models.Pooling"},
            {"idx": 2, "name": "2", "path": "2_Normalize", "type": "sentence_transformers.models.Normalize"},
        ],
        "1_Pooling/config.json": {"word_embedding_dimension": HIDDEN_SIZE, "pooling_mode_mean_tokens": True},
        "sentence_bert_config.json": {"max_seq_length": 256, "do_lower_case": False},
    },
    TINY_RERANKER: {
        "config.json": dict(
            _ENCODER,
            architectures=["XLMRobertaForSequenceClassification"],
            model_type="xlm-roberta",
            bos_token_id=0,
            eos_token_id=2,
            id2label={"0": "LABEL_0"},
            label2id={"LABEL_0": 0},
            layer_norm_eps=1e-05,
            # XLM-RoBERTa position ids start after the padding id.
            max_position_embeddings=514,
            type_vocab_size=1,
        ),
        "tokenizer.json": _tokenizer_json(single="<s> $A </s>", pair="<s> $A </s> </s> $B </s>"),
        "tokenizer_config.json": _TOKENIZER_CONFIG,
    },
}


def write_models() -> None:
    for model_dir, files in MODELS.items():
        for filename, content in files.items():
            path = Path(model_dir) / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(content, indent=2))

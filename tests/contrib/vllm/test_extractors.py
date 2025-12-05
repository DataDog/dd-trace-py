from ddtrace.contrib.internal.vllm.extractors import parse_prompt_to_messages


class TestParsePromptToMessages:
    """Tests for parse_prompt_to_messages function."""

    def test_empty_prompt(self):
        assert parse_prompt_to_messages("") == []
        assert parse_prompt_to_messages(None) == []

    def test_plain_text_fallback(self):
        """Unrecognized format returns raw prompt with empty role."""
        result = parse_prompt_to_messages("Hello world")
        assert len(result) == 1
        assert result[0] == {"role": "", "content": "Hello world"}

    def test_llama3_format(self):
        # https://www.llama.com/docs/model-cards-and-prompt-formats/meta-llama-3/
        prompt = (
            "<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n"
            "You are a helpful AI assistant for travel tips and recommendations<|eot_id|>"
            "<|start_header_id|>user<|end_header_id|>\n\n"
            "What is France's capital?<|eot_id|>"
            "<|start_header_id|>assistant<|end_header_id|>\n\n"
            "Bonjour! The capital of France is Paris!<|eot_id|>"
            "<|start_header_id|>user<|end_header_id|>\n\n"
            "What can I do there?<|eot_id|>"
            "<|start_header_id|>assistant<|end_header_id|>\n\n"
        )
        result = parse_prompt_to_messages(prompt)
        assert len(result) == 4
        assert result[0] == {
            "role": "system",
            "content": "You are a helpful AI assistant for travel tips and recommendations",
        }
        assert result[1] == {"role": "user", "content": "What is France's capital?"}
        assert result[2] == {"role": "assistant", "content": "Bonjour! The capital of France is Paris!"}
        assert result[3] == {"role": "user", "content": "What can I do there?"}

    def test_llama4_format(self):
        # https://www.llama.com/docs/model-cards-and-prompt-formats/llama4/
        prompt = (
            "<|begin_of_text|><|header_start|>system<|header_end|>\n\n"
            "You are a helpful assistant<|eot|>"
            "<|header_start|>user<|header_end|>\n\n"
            "Answer who are you in the form of jeopardy?<|eot|>"
            "<|header_start|>assistant<|header_end|>\n\n"
        )
        result = parse_prompt_to_messages(prompt)
        assert len(result) == 2
        assert result[0] == {"role": "system", "content": "You are a helpful assistant"}
        assert result[1] == {"role": "user", "content": "Answer who are you in the form of jeopardy?"}

    def test_granite_format(self):
        # https://www.ibm.com/docs/en/watsonx/saas?topic=solutions-prompt-lab
        prompt = (
            "<|start_of_role|>system<|end_of_role|>\n"
            "You are Granite, an AI assistant developed by IBM.<|end_of_text|>\n"
            "<|start_of_role|>user<|end_of_role|>\n"
            "What can you help me with?<|end_of_text|>\n"
            "<|start_of_role|>assistant<|end_of_role|>\n"
        )
        result = parse_prompt_to_messages(prompt)
        assert len(result) == 2
        assert result[0] == {"role": "system", "content": "You are Granite, an AI assistant developed by IBM."}
        assert result[1] == {"role": "user", "content": "What can you help me with?"}

    def test_granite_format_with_documents(self):
        # https://www.ibm.com/docs/en/watsonx/saas?topic=solutions-prompt-lab
        prompt = (
            "<|start_of_role|>system<|end_of_role|>\n"
            "You are a helpful assistant.<|end_of_text|>\n"
            "<|start_of_role|>document<|end_of_role|>\n"
            "Document content here.<|end_of_text|>\n"
            "<|start_of_role|>user<|end_of_role|>\n"
            "Summarize the document.<|end_of_text|>\n"
            "<|start_of_role|>assistant<|end_of_role|>\n"
        )
        result = parse_prompt_to_messages(prompt)
        assert len(result) == 3
        assert result[0] == {"role": "system", "content": "You are a helpful assistant."}
        assert result[1] == {"role": "document", "content": "Document content here."}
        assert result[2] == {"role": "user", "content": "Summarize the document."}

    def test_gemma_format(self):
        # https://ai.google.dev/gemma/docs/core/prompt-structure
        prompt = (
            "<start_of_turn>user\n"
            "knock knock<end_of_turn>\n"
            "<start_of_turn>model\n"
            "who is there<end_of_turn>\n"
            "<start_of_turn>user\n"
            "Gemma<end_of_turn>\n"
            "<start_of_turn>model\n"
            "Gemma who?<end_of_turn>\n"
        )
        result = parse_prompt_to_messages(prompt)
        assert len(result) == 4
        assert result[0] == {"role": "user", "content": "knock knock"}
        assert result[1] == {"role": "model", "content": "who is there"}
        assert result[2] == {"role": "user", "content": "Gemma"}
        assert result[3] == {"role": "model", "content": "Gemma who?"}

    def test_minimax_format(self):
        # https://platform.minimax.io/docs/guides/text-function-call
        prompt = (
            "<begin_of_document><beginning_of_sentence>system\n"
            "You are a helpful assistant.<end_of_sentence>\n"
            "<beginning_of_sentence>user\n"
            "What is the weather today?<end_of_sentence>\n"
            "<beginning_of_sentence>ai\n"
        )
        result = parse_prompt_to_messages(prompt)
        assert len(result) == 2
        assert result[0] == {"role": "system", "content": "You are a helpful assistant."}
        assert result[1] == {"role": "user", "content": "What is the weather today?"}
        # Trailing empty "ai" is skipped (generation prompt marker)

    def test_chatml_format(self):
        # https://qwen.readthedocs.io/en/latest/getting_started/concepts.html
        prompt = (
            "<|im_start|>system\n"
            "You are a helpful assistant.<|im_end|>\n"
            "<|im_start|>user\n"
            "Hello, how are you?<|im_end|>\n"
            "<|im_start|>assistant\n"
            "I'm doing well, thank you! How can I help you today?<|im_end|>\n"
            "<|im_start|>user\n"
            "Explain large language models.<|im_end|>\n"
            "<|im_start|>assistant\n"
        )
        result = parse_prompt_to_messages(prompt)
        assert len(result) == 4
        assert result[0] == {"role": "system", "content": "You are a helpful assistant."}
        assert result[1] == {"role": "user", "content": "Hello, how are you?"}
        assert result[2] == {"role": "assistant", "content": "I'm doing well, thank you! How can I help you today?"}
        assert result[3] == {"role": "user", "content": "Explain large language models."}

    def test_deepseek_vl2_format(self):
        # https://github.com/vllm-project/vllm/blob/main/vllm/transformers_utils/chat_templates/template_deepseek_vl2.jinja
        prompt = (
            "You are a helpful assistant.\n"
            "<|User|>: What is 2+2?\n\n"
            "<|Assistant|>: The answer is 4.\n\n"
            "<|User|>: And 3+3?\n\n"
            "<|Assistant|>: "
        )
        result = parse_prompt_to_messages(prompt)
        assert len(result) == 3
        assert result[0] == {"role": "user", "content": "What is 2+2?"}
        assert result[1] == {"role": "assistant", "content": "The answer is 4."}
        assert result[2] == {"role": "user", "content": "And 3+3?"}

    def test_phi_format_with_system(self):
        # https://github.com/vllm-project/vllm/blob/main/examples/tool_chat_template_phi4_mini.jinja
        prompt = (
            "<|system|>\n"
            "You are a helpful assistant.<|end|>\n"
            "<|user|>\n"
            "What is the meaning of life?<|end|>\n"
            "<|assistant|>\n"
        )
        result = parse_prompt_to_messages(prompt)
        assert len(result) == 2
        assert result[0] == {"role": "system", "content": "You are a helpful assistant."}
        assert result[1] == {"role": "user", "content": "What is the meaning of life?"}

    def test_phi_format_with_user(self):
        # https://github.com/vllm-project/vllm/blob/main/examples/tool_chat_template_phi4_mini.jinja
        prompt = "<|user|>\nHello there!<|end|>\n<|assistant|>\n"
        result = parse_prompt_to_messages(prompt)
        assert len(result) == 1
        assert result[0] == {"role": "user", "content": "Hello there!"}

    def test_deepseek_v3_format(self):
        # https://huggingface.co/deepseek-ai/DeepSeek-V3.1
        # Uses fullwidth ｜ (U+FF5C)
        prompt = "<｜begin▁of▁sentence｜>You are a helpful assistant<｜User｜>Who are you?<｜Assistant｜>"
        result = parse_prompt_to_messages(prompt)
        assert len(result) == 1
        assert result[0] == {"role": "user", "content": "Who are you?"}

    def test_teleflm_format_with_user(self):
        # https://github.com/vllm-project/vllm/blob/main/examples/template_teleflm.jinja
        prompt = "<_user>What time is it?<_bot>"
        result = parse_prompt_to_messages(prompt)
        assert len(result) == 1
        assert result[0] == {"role": "user", "content": "What time is it?"}

    def test_teleflm_format_with_system(self):
        # https://github.com/vllm-project/vllm/blob/main/examples/template_teleflm.jinja
        prompt = "<_system>You are a helpful assistant.<_user>What time is it?<_bot>"
        result = parse_prompt_to_messages(prompt)
        assert len(result) == 2
        assert result[0] == {"role": "system", "content": "You are a helpful assistant."}
        assert result[1] == {"role": "user", "content": "What time is it?"}

    def test_inkbot_format_with_user(self):
        # https://github.com/vllm-project/vllm/blob/main/examples/template_inkbot.jinja
        prompt = "<#user#>\nHow can I help you today?\n<#bot#>\n"
        result = parse_prompt_to_messages(prompt)
        assert len(result) == 1
        assert result[0] == {"role": "user", "content": "How can I help you today?"}

    def test_inkbot_format_with_system(self):
        # https://github.com/vllm-project/vllm/blob/main/examples/template_inkbot.jinja
        prompt = "<#system#>\nYou are a helpful assistant.\n<#user#>\nHow can I help you today?\n<#bot#>\n"
        result = parse_prompt_to_messages(prompt)
        assert len(result) == 2
        assert result[0] == {"role": "system", "content": "You are a helpful assistant."}
        assert result[1] == {"role": "user", "content": "How can I help you today?"}

    def test_alpaca_format(self):
        # https://github.com/vllm-project/vllm/blob/main/examples/template_alpaca.jinja
        prompt = "### Instruction:\nWrite a poem about the sea.\n\n### Response:\n"
        result = parse_prompt_to_messages(prompt)
        assert len(result) == 1
        assert result[0] == {"role": "instruction", "content": "Write a poem about the sea."}

    def test_falcon_format(self):
        # https://github.com/vllm-project/vllm/blob/main/examples/template_falcon.jinja
        prompt = "User: What is the capital of France?\nAssistant:"
        result = parse_prompt_to_messages(prompt)
        assert len(result) == 1
        assert result[0] == {"role": "user", "content": "What is the capital of France?"}

    def test_skips_empty_trailing_assistant(self):
        """Empty trailing assistant message should be skipped."""
        prompt = "<|im_start|>user\nHello<|im_end|>\n<|im_start|>assistant\n<|im_end|>"
        result = parse_prompt_to_messages(prompt)
        assert len(result) == 1
        assert result[0] == {"role": "user", "content": "Hello"}

    def test_preserves_multiline_content(self):
        """Content with newlines should be preserved."""
        prompt = (
            "<|im_start|>system\n"
            "Line 1\nLine 2\nLine 3<|im_end|>\n"
            "<|im_start|>user\n"
            "Question<|im_end|>\n"
            "<|im_start|>assistant\n"
        )
        result = parse_prompt_to_messages(prompt)
        assert len(result) == 2
        assert result[0] == {"role": "system", "content": "Line 1\nLine 2\nLine 3"}

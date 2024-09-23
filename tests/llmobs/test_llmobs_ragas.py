import ragas

from ddtrace.llmobs._evaluators.ragas.faithfulness import RagasFaithfulnessEvaluator


def test_ragas_evaluator_init(LLMObs):
    rf_evaluator = RagasFaithfulnessEvaluator(LLMObs)
    assert rf_evaluator.enabled == True
    assert rf_evaluator.llmobs == LLMObs
    assert rf_evaluator.faithfulness == ragas.faithfulness
    assert rf_evaluator.faithfulness.llm == ragas.llms.llm_factory()


def test_ragas_faithfulness_returns_none_if_disabled(LLMObs):
    pass


def test_ragas_faithfulness_returns_none_if_inputs_extraction_fails(LLMObs):
    pass


def test_ragas_faithfulness_returns_none_if_no_statements_in_answer(LLMObs):
    pass


def test_ragas_faithfulness():
    pass


def test_ragas_faithfulness_emits_traces():
    pass


def test_ragas_faithfulness_emits_traces():
    pass


def test_extract_faithfulness_inputs_question_mising():
    pass


def test_extract_faithfulness_inputs_context_mising():
    pass


def test_score_faithfulness():
    # set up cassette for this
    pass

from langchain.agents import AgentType
from langchain.agents import initialize_agent
from langchain.llms.fake import FakeListLLM
from langchain.tools import ShellTool


def patch_langchain(prompt):
    responses = ["Action: terminal\nAction Input: echo Hello World", "Final Answer: 4"]
    llm = FakeListLLM(responses=responses)
    shell = ShellTool()
    shell_chain = initialize_agent([shell], llm, agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION, verbose=True)
    # label test_openai_llm_appsec_iast_cmdi
    return shell_chain.run(prompt)

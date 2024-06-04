import os
import re

import vcr


# VCR is used to capture and store network requests made to Anthropic.
# This is done to avoid making real calls to the API which could introduce
# flakiness and cost.


# To (re)-generate the cassettes: pass a real Anthropic API key with
# ANTHROPIC_API_KEY, delete the old cassettes and re-run the tests.
# NOTE: be sure to check that the generated cassettes don't contain your
#       API key. Keys should be redacted by the filter_headers option below.
# NOTE: that different cassettes have to be used between sync and async
#       due to this issue: https://github.com/kevin1024/vcrpy/issues/463
#       between cassettes generated for requests and aiohttp.
def get_request_vcr():
    return vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes"),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "x-api-key", "api-key"],
        # Ignore requests to the agent
        ignore_localhost=True,
    )


# Anthropic Tools


def calculate(expression):
    # Remove any non-digit or non-operator characters from the expression
    expression = re.sub(r'[^0-9+\-*/().]', '', expression)
    
    try:
        # Evaluate the expression using the built-in eval() function
        result = eval(expression)
        return str(result)
    except (SyntaxError, ZeroDivisionError, NameError, TypeError, OverflowError):
        return "Error: Invalid expression"
    

tools = [
    {
        "name": "calculator",
        "description": "A simple calculator that performs basic arithmetic operations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "The mathematical expression to evaluate (e.g., '2 + 3 * 4')."
                }
            },
            "required": ["expression"]
        }
    }
]


def process_tool_call(tool_name, tool_input):
    if tool_name == "calculator":
        return calculate(tool_input["expression"])

MODEL = "jev-1.13.0"
STATE = "I was charged twice for my subscription this month and want the duplicate refunded."
# Distinct from STATE so the error request gets its own cassette
ERROR_STATE = "Please cancel my account."
EXPECTED_QUESTIONS = {
    "team": {
        "type": "choice",
        "instructions": "Which team should handle this message?",
        "criteria": {
            "billing": "Charges, refunds, invoices",
            "technical": "Bugs, outages, errors",
            "sales": "Pricing, plans, buying more",
        },
    },
    "refund": {"type": "noul", "instructions": "Is the customer asking for a refund?"},
    "urgency": {"type": "score", "instructions": "How urgent is this message?", "criteria": ["Low", "Medium", "High"]},
}


def build_questions(typesafe_sdk):
    team, refund, urgency = EXPECTED_QUESTIONS["team"], EXPECTED_QUESTIONS["refund"], EXPECTED_QUESTIONS["urgency"]
    return {
        "team": typesafe_sdk.Choice(instructions=team["instructions"], criteria=team["criteria"]),
        "refund": typesafe_sdk.Noul(instructions=refund["instructions"]),
        "urgency": typesafe_sdk.Score(instructions=urgency["instructions"], criteria=urgency["criteria"]),
    }

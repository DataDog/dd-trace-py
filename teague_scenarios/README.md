Docker: https://docker-py.readthedocs.io/en/stable/
Python on Whales: https://gabrieldemarmiesse.github.io/python-on-whales/sub-commands/compose/#python_on_whales.components.compose.cli_wrapper.ComposeCLI.config


Scenarios:
- Run some things that happen, and make sure it did the things you wanted


HTTP Frameworks:
    - Scenario 1: Basic
        - Description: Call a simple endpoint on an application that succeeds
        - Validations:
            - 

Validations:
    - Things which should be true



Issues:
- Negative rate doesn't mean anything to me (unactioanble).  Ended up not mattering, but getting this from the agent for short-lived docker containers
- Didn't set DD_ENV for the load generator - had no idea what was going on (ended up looking at above before I figured it out)
    - Played with keeping the service up and running, not clear if "no data received" or "no trace" or "configured wrong"
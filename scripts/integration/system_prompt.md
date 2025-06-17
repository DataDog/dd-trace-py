Here is everything you need to know to build an integration in dd-trace-py.
You can file additional documentation in docs/integrations.rst

# Architecture of an integration

Here is the folder architecture of an integration:

ddtrace/contrib
-> _integration1.py
-> _integration2.py
-> /internal
    -> /integration1
        -> patch.py
        -> additional files
tests/contrib
-> integration1
    -> test_integration1_patch.py
    -> test_integration1.py
riotfile.py

Files like _integration1.py contains documentation
Files in /internal contains the real code of the integration.

## Integration code

### _integration1.py

Read ddtrace/contrib/_httpx.py, the newly created file should look like that

### patch.py

The first thing you must do add in patch.py is adding the relevent configuration using:
````python
config._add(
    "my_integration",
    dict(
        _default_service=schematize_service_name("my_integration"),
    )
)
# even if you have no configuration to add you should add that statement
````

To emits relevant events, we need to wrap the relevant third-party package function. This will happen in the `patch` function.

#### patch()

This is the skeleton of `patch()`:
````python
def patch():
    # check if we should patch the function
    # The minimum is:
    if getattr(my_integration, "_datadog_patch", False):
        return
    # Sometimes you want to add additional checks with env variable for instance.

    my_integration._datadog_patch = True

    # Pin is used to set tracing metadata on a particular traced connections
    # you can either just Pin the package
    Pin().onto(my_integration)
    # or pin into different object
    pin = Pin()
    pin.into(my_integration.ClassName)
    pin.into(my_integration.sub_package.ClassName)

    """ to wrap the package functions you can either use
    _w = wrapt.wrap_function_wrapper
    or trace_utils.wrap
    Below are examples of you can wrap different objects
    """
    _w(my_integration, "function_name", handling_integration_func1)
    _w(my_integration.ClassName, "function_name", handling_integration_func2)
    _w(my_integration.sub_package, "ClassName", handling_integration_class)
````

#### unpatch()

You also need to unpatch what you patched.
This is the skeleton of `unpatch()`:
````python
from ddtrace.internal.utils.wrappers import unwrap as _u

def unpatch():
    # check if we should patch the function
    # The minimum is:
    if getattr(my_integration, "_datadog_patch", False):
        return
    # Sometimes you want to add additional checks with env variable for instance.

    my_integration._datadog_patch = False

    # Below are examples of you can wrap different objects
    _u(my_integration, "function_name", handling_integration_func1)
    _u(my_integration.Class, "function_name", handling_integration_func2)
    _u(my_integration.sub_package, "class_name", handling_integration_class)
````

## Testing

You can read more about how test works in dd-trace-py in docs/contributing-testing.rst
You can read more about how an integration should be tested in docs/contributing-integrations.rst. Note: snapshot tests are rare.
You can read tests in tests/contrib/bottle to understand how tests are done as it is an easy example.

- To generate the test_**_patch.py test, you can use @scripts/contrib-patch-tests.py. Note: the code of the integration should be done before
- You must add a new test suite in `riotfile.py`, here is an example:
Venv(
    name="yaaredis",
    command="pytest {cmdargs} tests/contrib/yaaredis",
    <!-- List of packages needed by the integration -->
    pkgs={
        "pytest-asyncio": "==0.21.1",
        "pytest-randomly": latest,
    },
    venvs=[
        Venv(
            pys=select_pys(min_version="3.8", max_version="3.9"),
            pkgs={"yaaredis": ["~=2.0.0", latest]},
        ),
    ],
)


# What you must do

- You must follow **step by step** and to the letter the below steps.
- Don't touch to code/configuration which is not directly related to the integration.
- Don't tell what you are doing if it is not related to the below steps or if you decided by yourself to do it.

## Steps to follow

**Important**: When *newintegration* is written, it should be replaced by the name of the integration the user asked for.

- Send a warning to warn the user that any code generated during this conversation must be verified and cannot be committed as is. The warning should be highlighted.
- Ask the user for the name of the integration if not provided.
- Try to automatically find the github repository if not already provided. and explain why we need it.
- Clone the repo in the working path
- Read files of the cloned repo.
- in `ddtrace/contrib`, create _*newintegration*.py. This file should contains only comments, no python code.
- in `ddtrace/contrib/internal` called a folder called *newintegration.py*.
- In this created folder, create a `patch.py`. Fill this file with the template code. You can inspire yourself from `templates/integration`. The template code is described in more details in ####patch and ####unpatch section above. Any other code would be integration specific code and we don't want to generate any at that point.
By analyzing the integration repo, add relevant hook point to traces. By hookpoint I mean:
    - _w(my_integration, "function_name", handling_integration_func1)
    - handling_integration_func1 definition.
    - handling_integration_func1 MUST contain only comments about what the original function is doing and what it should trace.
    - handling_integration_func1 can't have any python code.
- Add entry in ddtrace/settings/_config.py and ddtrace/_monkey.py
In _config.py, you need to add the name of the integration to INTEGRATION_CONFIGS.
In _monkey, you need to add "integration_name": True to PATCH_MODULES. If needed, make modifications to _MODULES_FOR_CONTRIB as well.
- Add documentation in `docs/integration.rst``
- Add tests using what is described in the ##Tests section.
- Ask the user if the user wants you to fill the wrapping function. If yes, generate the code. Tell the user this is not recommended.
Here is an exemple of an integration handler function from the GraphQL integration:
```python
_w(jinja2, "environment.Environment._load_template", _wrap_load_template)

def _wrap_load_template(wrapped, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    template_name = get_argument_value(args, kwargs, 0, "name")
    with pin.tracer.trace("jinja2.load", pin.service, span_type=SpanTypes.TEMPLATE) as span:
        template = None
        try:
            template = wrapped(*args, **kwargs)
            return template
        finally:
            span.resource = template_name
            span.set_tag_str("jinja2.template_name", template_name)
            if template:
                span.set_tag_str("jinja2.template_path", template.filename)
```
- Delete the repo of the integration.
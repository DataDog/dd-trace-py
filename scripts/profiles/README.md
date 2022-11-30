# Profile Scenarios

This folder contains profile scenarios for overhead analysis and validation.

## Usage

To run a scenario, execute the setup stage with

~~~
scripts/profiles/<scenario>/setup.sh <prefix>
~~~

from the root folder of the local `ddtrace` source repository. Then run the
actual scenario with

~~~
scripts/profiles/<scenario>/run.sh <prefix>
~~~

Make sure to use the same `<prefix>` folder with both scripts. This is the place
where all the dependencies will be (re-)installed. At the end of the run, all
the artifacts produced will be in `<prefix>/artifacts`.

### Example

~~~
scripts/profiles/django-simple/setup.sh /tmp/delme \
&& scripts/profiles/django-simple/run.sh /tmp/delme
~~~

## Artifacts

Typically, a profile scenario will produce some `*.austin` files containing the
raw samples collected during the scenario run. The best way to use these files
is together with the [VS Code extension for Austin][austin_vscode], which
provide interactive source navigation, heat map visualisations, sampled call
stacks and top function views.


[austin_vscode]: (https://marketplace.visualstudio.com/items?itemName=p403n1x87.austin-vscode)

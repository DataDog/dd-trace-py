## `ddtrace` profiling tools

This folder contains scripts and tools to profile `ddtrace`, see flamegraphs and found bottlenecks.

The main tools are:
- [py-spy](https://github.com/benfred/py-spy): a sampling profiler for Python programs
- [apache benchmark](https://httpd.apache.org/docs/2.4/programs/ab.html): ab is a tool for benchmarking http servers
- [speedscope](https://github.com/jlfwong/speedscope): interactive flamegraph visualizer


# How to profile function

Create a file in script folder profiling/scripts/prof_[scenario].py

Execute:
```
./run [scenario]
```

The results will store in:
```
profiling/results/prof_[scenario].svg
```

You can see the results with `speedscope`:

```
docker-compose up speedscope
http://localhost:8080/
```

# Profile Application

To profile a real application with dd-trace, execute the below script:

```                  
export DD_SITE=[SITE]
export DD_API_KEY=[APIKEY]
export DD_SERVICE=alberto.vara.realworld
# ======[OPTIONAL]======
# export DDTRACE_INSTALL_VERSION="git+https://github.com/Datadog/dd-trace-py@1.x"
# export DDTRACE_INSTALL_VERSION="git+https://github.com/Datadog/dd-trace-py@mybranc/feature"
# export DDTRACE_INSTALL_VERSION="ddtrace==1.4.4"
# ======[        ]======
docker-compose build
docker-compose run real_world_app
```

Then, generate traffic in the app:

```
sudo apt-get install -y apache2-utils
ab -n 2000 -c 50 http://127.0.0.1:3000/api/articles?token=secret
ab -n 2000 -c 50 http://127.0.0.1:3000/api/tags 
```

profile application with:

```
docker exec -it $(docker ps -f name=app_real_world --format "table {{.Names}}" | tail -1) /bin/bash
export VENV_DDTRACE=/app/.venv_ddtrace/
source ${VENV_DDTRACE}/bin/activate

py-spy record --native --format speedscope --rate 300 -o /profiles/profile_app.prof --pid $(ps -ef | awk '/uwsgi/{print $2}' | head -1)
```

Results:

```
profiling/app/profiles/profile_prof_set_http_meta_enabled.svg
```
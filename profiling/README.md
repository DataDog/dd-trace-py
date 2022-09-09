## Profiling

# Profile function

```
./run [scenario]

profiling/appsec/prof_[scenario].py
```

Results:

```
profiling/results/prof_set_http_meta_enabled.svg
```
# Profile Application

```              
cd app/         
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

```
docker exec -it $(docker ps -f name=app_real_world --format "table {{.Names}}" | tail -1) /bin/bash
export VENV_DDTRACE=/app/.venv_ddtrace/
source ${VENV_DDTRACE}/bin/activate

py-spy record --native --format speedscope --rate 300 -o /profiles/profile_app.prof --pid $(ps -ef | awk '/uwsgi/{print $2}' | head -1)
docker-compose run speedscope

sudo apt-get install -y apache2-utils
ab -n 2000 -c 50 http://127.0.0.1:3000/api/articles?token=secret
```
```
docker-compose run speedscope
http://localhost:8080/
```
Results:

```
profiling/app/profiles/profile_prof_set_http_meta_enabled.svg
```
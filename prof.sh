source venv311/bin/activate

export DD_SITE=datadoghq.com
export DD_PROFILING_ENABLED=true
export DD_VERSION=$(date +"%Y-%m-%d-%H-%M")
export DD_SERVICE=whatever.py

ddtrace-run python3 whatever.py

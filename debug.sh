source venv311/bin/activate

export DD_SITE=datadoghq.com
export DD_PROFILING_ENABLED=true
export DD_VERSION=$(date +"%Y-%m-%d-%H-%M")
export DD_SERVICE=whatever.py

which_ddtrace=$(which ddtrace-run)
which_python=$(which python3)

echo "r $which_ddtrace $which_python whatever.py"
gdb $which_python

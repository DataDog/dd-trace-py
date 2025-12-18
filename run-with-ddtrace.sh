export DD_SITE=datadoghq.com
export DD_PROFILING_ENABLED=true
export DD_VERSION=$(date +"%Y-%m-%d-%H-%M")
export DD_SERVICE=kowalski-python

export DD_PROFILING_UPLOAD_INTERVAL=15

export ECHION_USE_FAST_COPY_MEMORY=1

echo "https://ddstaging.datadoghq.com/profiling/explorer?query=host%3A%2Akowalski%2A%20service%3Akowalski-python%20version%3A$DD_VERSION&agg_m=%40prof_core_cpu_cores&agg_m_source=base&agg_q=service&agg_q_source=base&agg_t=sum&fromUser=false&my_code=enabled&refresh_mode=sliding&top_n=100&top_o=top&viz=stream&x_missing=true"

# Pass all args
source venv311/bin/activate
ddtrace-run "$@"

# python3 whatever_async.py > log6.log  2>&1^C

# gdb $(which python3)
# /home/bits/go/src/github.com/DataDog/dd-trace-py/venv311/bin/ddtrace-run
# /home/bits/go/src/github.com/DataDog/dd-trace-py/venv311/bin/python3

# handle SIGSEGV nostop noprint pass
# run /home/bits/go/src/github.com/DataDog/dd-trace-py/venv311/bin/ddtrace-run /home/bits/go/src/github.com/DataDog/dd-trace-py/venv311/bin/python3 whatever_async.py  > log6.log  2>&1

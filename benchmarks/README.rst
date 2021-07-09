Performance Testing
===================

New scenario
------------

Copy ``sample`` directory and edit the ``meta.yaml`` file to specify the executable. Add any additional requirements to the ``requirements.txt``.

Build image::

  docker build -t benchmark-<scenario> --build-arg BENCHMARK=<scenario> -f base.Dockerfile .

Run scenario::

  docker run -it --rm benchmark-<scenario>

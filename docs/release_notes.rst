Release Notes
=============

..
    Load all release notes from the current branch when spell checking
    DEV: Without this we won't get spell checking on PRs or release
         notes that are not yet on a release branch.
    DEV: We generate the notes in a separate file to avoid any refs/directives
         colliding with the official notes. However, in order to get sphinx to
         not complain it must also exist in a toctree somewhere, so we add here
         hidden.

.. only:: spelling

    .. toctree::
        :hidden:

        _release_notes_all


.. ddtrace-release-notes::


Prior Releases
--------------
Release notes prior to v0.44.0 can be found in `CHANGELOG.md
<https://github.com/DataDog/dd-trace-py/blob/master/CHANGELOG.md>`_ in the root
of the repository.

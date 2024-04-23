..
  SPDX-FileCopyrightText: 2021 The PyPSA meets Earth authors

  SPDX-License-Identifier: CC-BY-4.0

.. _how_to_contribute:

##########################################
Сontribute
##########################################

We welcome and really value contributions! Every contribution is valuable, and you always get credits.

Contributions to the code can be made in several ways:

- submit feedback,
- add new features,
- report bugs, 
- fix bugs, 
- implement a new cluster/cloud computation backend,
- write documentation


Code
====

For every code contribution you should run `pre-commit <https://pre-commit.com/index.html>`_.
This will lint, format and check your code contributions against our guidelines
(e.g. we use `Black <https://github.com/psf/black>`_ as code style
and aim for `REUSE compliance <https://reuse.software/>`_):

1. Installation ``conda install -c conda-forge pre-commit`` or ``pip install pre-commit``
2. Usage:
  * To automatically activate ``pre-commit`` on every ``git commit``: Run ``pre-commit install``
  * To manually run it: ``pre-commit run --all``

Add a new test if you want to contribute new functionality to the config.
We perform currently *multiple* integration tests which means various workflows need to work.
All test configs are build by updating the ``config.tutorial.yaml`` with the configs in ``pypysa-za/test/*.yaml``.

  * You can test your contribution locally with ``snakemake --cores 4 run_tests``. This will build test configs and executes them.
  * Run ``snakemake -j1 build_test_configs`` to build and analyse locally the test configs.

To contribute a test:

1. Provide a new test in ``test/<new test>.yaml``, or adjust one of the existing ones. These tests update the config.tutorial.yaml to test other options e.g. landlock countries. 
2. Add a new test config path to the ``rule build_all_test`` in the ``Snakefile``.
3. If your functionality should be tested in the CI for every pull request, add a respective code in ``.github/workflows/ci-linux.yaml``. We test all functionalities only for Linux while providing a general test for windows and mac.


No-Code
========
Instead of contributing code there are alternatives to support the PyPSA-RSA goals.
You can fund projects, supervise students, support us with outreach activities or events.

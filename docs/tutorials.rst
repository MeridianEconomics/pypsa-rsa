..
  SPDX-FileCopyrightText: 2021 The PyPSA meets Earth authors

  SPDX-License-Identifier: CC-BY-4.0

.. _tutorial:


##########################################
Tutorial
##########################################

This tutorial introduces the **PyPSA-RSA** energy system model and demonstrates how to configure and run it locally using reduced datasets. It provides a lightweight workflow that explores most of the model's capabilities with limited computing resources.

Before getting started with **PyPSA-RSA** it makes sense to be familiar
with its general modelling framework `PyPSA <https://pypsa.readthedocs.io>`__, and ensure the :ref:`installation` is complete.

The tutorial will cover how to:

- **customise model scenarios**
- **configure the model** using `config.yaml` and spreadsheets
- **execute the workflow** using Snakemake
- **analyse results** using Python

Configuring the Tutorial Environment
===============================================
The ``scenarios_to_run.xlsx`` and ``config.yaml`` files are utilised to customise the PyPSA-RSA model.

The tutorial uses modified versions of the main configuration files:

.. code:: bash

    .../pypsa-rsa % cp config.yaml config.tutorial.yaml
    .../pypsa-rsa % cp scenarios_to_run.xlsx scenarios_to_run_tutorial.xlsx
..

Scenario Setup: ME IRP 2024
===============================================

Scenarios are defined via spreadsheet inputs in the directory:

``scenarios/ME IRP 2024/sub scenarios``

Key input files include:

- `annual_load.xlsx`: Annual load (TWh/year) from 2019–2050 (e.g., IRP 2023, CSIR-Meridian Ambitions)
- `carbon_constraints.xlsx`: CO₂ emissions limits (Mt/year)
- `extendable_technologies.xlsx`: New build generation and storage technologies
- `fixed_technologies.xlsx`: Existing generation and storage technologies
- `operational_constraints.xlsx`: Technology new build constraints, gas contracting, security of supply and system adequacy
- `plant_availability.xlsx`: Generator availability
- `reserve_margin.xlsx`: Reserve requirements
- `transmission_expansion.xlsx`: Transmission expansion plans

Scenarios are assembled in `scenarios_to_run.xlsx`, referencing parameters in the above files. Each scenario can be toggled in the file's `run_scenario` column.

Model Configuration: config.yaml
===============================================

The main model setup is governed by `config.yaml`. It includes:

- **Scenario file and folder paths**
- **Reference years** for load/weather data
- **CRS settings** (coordinate systems)
- **Topology build flags**
- **Solver settings**

The tutorial inputs must be modified to use the tutorial scenarios by setting the `scenarios_to_run_file` and `scenarios_folder` tags in the `config.tutorial.yaml` file.

.. code-block:: yaml

    scenarios:
      folder: ME IRP 2024
      scenarios_folder: "scenarios_to_run_tutorial.xlsx"

Within the configuration file, the crs (coordinate reference system) section defines the spatial projections used for geolocation, distance, and area calculations. These are typically based on standard projection systems and are rarely modified during model setup.

Relevant year data to the model such as the reference load year and reference weather years are defined in the configuration file. 

The reference load year is used to define the load profile for the model, while the reference weather years are used to define the renewable resource profiles.
The number of years analysed can also be changed, for instance, using 2-year increments over a given time horizon.

.. literalinclude:: ../config.yaml
    :language: yaml
    :start-at: years:
    :end-before: wind_offshore

Within the single node profiles, various regions can be isolated and selected for renewable resource potentials. 

.. literalinclude:: ../config.yaml
    :language: yaml
    :start-at: single_node_profiles:
    :end-before: wind_offshore:

Finally, it is possible to pick a solver. For instance, this tutorial uses the open-source solvers HiGHS and does not rely
on the commercial solvers Gurobi or CPLEX (for which free academic licenses are available). Other open-source solvers such as GLPK or CBC can also be used.

.. literalinclude:: ../config.yaml
   :language: yaml
   :start-at: solving:
   :end-before: Crossover:

.. note::

    To run the tutorial, install HiGHS (see instructions for :ref:`installation`).

    Alternatively, choose another installed solver in the ``config.yaml`` at ``solving: solver:``.

How to run the tutorial?
===============================================

To run the tutorial, create a copy of the configuration file and the scenarios to run file.

.. code:: bash

    .../pypsa-rsa (pypsa-rsa) % cp config.yaml config.tutorial.yaml
    .../scenarios/ME IRP2024 (pypsa-rsa) % cp scenarios_to_run.xlsx scenarios_to_run_tutorial.xlsx

Change the configuration file to use the tutorial scenarios by setting the ``scenarios_to_run_file`` and ``scenarios_folder`` tags in the ``config.tutorial.yaml`` file. 
Make the desired changes to the configuration file and scenarios to run as mentioned above. Once that is done, ensure that the run_scenario column for the scenarios of interest is set to **TRUE** in the ``scenarios_to_run_tutorial.xlsx`` file.

How to execute different parts of the workflow?
===============================================

Snakemake is a workflow management tool inherited by PyPSA-RSA from PyPSA-Eur.
Snakemake decomposes a large software process into a set of subtasks, or 'rules', that are automatically chained to obtain the desired output.

.. note::

  ``Snakemake``, which is one of the major dependencies, will be automatically installed in the environment pypsa-rsa, thereby there is no need to install it manually.

The snakemake included in the conda environment pypsa-rsa can be used to execute any custom rule with the following command:

.. code:: bash

    .../pypsa-rsa (pypsa-rsa) % snakemake <--cores all solve_all_scenarios --resources solver_slots=1>

The ``--cores all`` option allows the user to use all available cores for parallel execution, while the ``--resources solver_slots=1`` option limits the number of solver slots to 1, which is useful when using a single-core solver like HiGHS.

This will show up as a list of jobs that are executed in the terminal, and the output will be saved in the ``results`` folder.

.. code:: console

    Building DAG of jobs...
    Using shell: /bin/bash
    Provided resources: solver_slots=1
    Rules claiming more threads will be scaled down.
    Job stats:
    job                    count
    -------------------  -------
    solve_all_scenarios        1
    total                      1

    Select jobs to execute...
    Execute 1 jobs...
    localrule solve_all_scenarios:
        output: results/solve_all_scenarios_complete
        jobid: 0
        reason: Forced execution
        resources: tmpdir=/tmp
    Touching output file results/solve_all_scenarios_complete.
    Finished jobid: 0 (Rule: solve_all_scenarios)
    1 of 1 steps (100%) done

How to analyse results?
===============================================
The results of the model can be analysed in a Jupyter notebook as: 
.. code:: python

    import pypsa
    n = pypsa.Network("results/ME IRP 2024/network/capacity-IRP_REF_CI.nc")

.. include:: ./how_to_docs.rst

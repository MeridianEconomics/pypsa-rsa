..
  SPDX-FileCopyrightText: 2021 The PyPSA meets Earth authors

  SPDX-License-Identifier: CC-BY-4.0

.. _tutorial:


##########################################
Tutorial
##########################################

Before getting started with **PyPSA-RSA** it makes sense to be familiar
with its general modelling framework `PyPSA <https://pypsa.readthedocs.io>`__.

The tutorial uses fewer computing resources than the entire model, allowing the user to explore 
the majority of its features on a local computer.

.. 
    It takes approximately five minutes to complete and requires 3 GB of memory along with 1 GB free disk space.

If not yet completed, follow the :ref:`installation` steps first.

The tutorial will cover examples on how to

- configure and customise the PyPSA-RSA model and
- step-by-step execution of the ``snakemake`` workflow, from network creation through solving the network to analysing the results.

The ``scenarios_to_run.xlsx`` and ``config.yaml`` files are utilised to customise the PyPSA-RSA model. Use the configuration and model setup files ``config.yaml`` and ``model_file.xlsx`` to run the tutorial

.. code:: bash

    .../pypsa-rsa % cp config.yaml config.tutorial.yaml
    .../pypsa-rsa % cp scenarios_to_run.xlsx scenarios_to_run_tutorial.xlsx

..
    This configuration is set to download a reduced data set via the rules :mod:`retrieve_databundle`,
    :mod:`retrieve_natura_raster`, :mod:`retrieve_cutout` totalling at less than 250 MB.
    The full set of data dependencies would consume 5.3 GB.
    For more information on the data dependencies of PyPSA-RSA, continue reading :ref:`data`.

How to customise PyPSA-RSA?
=============================

Model setup: ME IRP 2024
----------------------------

The **ME IRP 2024** folder contains the following files:

* annual_load.xlsx
* carbon_constraints.xlsx
* extendable_technologies.xlsx
* fixed_technologies.xlsx
* operational_constraints.xlsx
* plant_availability.xlsx
* reserve_margin.xlsx
* transmission_expansion.xlsx 

These define the parameters that make up a given scenario, and the scenarios to run are found in scenarios/ME IRP 2024/scenarios_to_run.xlsx


Configuration: config.yaml
----------------------------

The model can be further adapted using the ``config.yaml`` to only include a select number of ``regions`` (e.g. ``1-supply``, ``11-supply`` or ``27-supply``). The tutorial is setup to run the 
``1-supply`` which uses a single node for the entire country.

.. literalinclude:: ../config.tutorial.yaml
   :language: yaml
   :start-at: scenario:
   :end-before: resarea:

The model uses the ``regions`` selected to determine the network topology. When the option ``build_topology`` is enabled, the model constructs the network topology. It is necessary to enable this 
when running the model for the first time or when changing the ``regions`` tag. 

.. literalinclude:: ../config.tutorial.yaml
   :language: yaml
   :start-at: build_topology:
   :end-before: build_cutout:

PyPSA-RSA provides several methods for generating renewable resource data. This is defined under electricity.renewable_generators in the configuration. Eskom data is used for bioenergy, hydro, and hydro import, 
WASA data is used for wind resources, ERA5 data is used for offshore wind resources, and SARAH data is used for solar resources.
Temporal and spatial availability of renewables such as wind and solar energy are built using historical weather data through electricity.renewable_generators.resource_profiles. 

.. literalinclude:: ../config.tutorial.yaml
   :language: yaml
   :start-at: use_eskom_wind_solar:
   :end-at: build_renewable_profiles:

Historical weather data is used and thus the year in which the data was obtained is specified for each carrier under years:reference_weather_years in the configuration file.

.. literalinclude:: ../config.tutorial.yaml
   :language: yaml
   :start-at: reference_weather_years:
   :end-before: electricity:


The cutout is configured in the `prepare_atlite_cutouts.ipynb` notebook. The options below can be adapted to download weather data for the required range of coordinates surrounding South Africa.
For more details on `atlite` please follow the `tutorials <https://atlite.readthedocs.io/en/latest/examples/create_cutout.html>`_. Once the historical weather data is downloaded, `atlite` is used to convert the weather data to power systems data.

.. literalinclude:: ../config.tutorial.yaml
   :language: yaml
   :start-at: atlite:
   :end-before: renewable:

The spatial resolution of the downloaded ERA5 dataset is given on a 30km x 30km grid. For wind power generation, this spatial resolution is not enough to resolve the local
dynamics. The notebook `prepare_extendable_wind.ipynb` uses global wind atlas mean wind speed at 100m to correct the ERA5 data.


.. literalinclude:: ../config.tutorial.yaml
   :language: yaml
   :start-after: renewable:
   :end-at: capacity_per_sqkm:

Solar PV profiles are generated using pre-defined or custom panel properties in the `prepare_fixed_solar.ipynb` notebook.

.. literalinclude:: ../config.tutorial.yaml
   :language: yaml 
   :lines: 170-177

The renewable potentials are calculated for eligible land, excluding the conservation and protected areas.
 
.. literalinclude:: ../config.tutorial.yaml
   :language: yaml
   :start-at: salandcover:
   :end-at: clip_p_max_pu:

In addition, the expansion of renewable resources is limited to either the `REDZ` regions or areas close to the strategic transmission `corridors`.

.. literalinclude:: ../config.tutorial.yaml
   :language: yaml
   :lines: 10

Finally, it is possible to pick a solver. For instance, this tutorial uses the open-source solvers CBC and Ipopt and does not rely
on the commercial solvers Gurobi or CPLEX (for which free academic licenses are available).

.. literalinclude:: ../config.tutorial.yaml
   :language: yaml
   :start-at: solving:
   :end-before: plotting:

.. note::

    To run the tutorial, either install CBC and Ipopt (see instructions for :ref:`installation`).

    Alternatively, choose another installed solver in the ``config.yaml`` at ``solving: solver:``.

Note, that we only note major changes to the provided default configuration that is comprehensibly documented in :ref:`config`.
There are many more configuration options beyond what is adapted for the tutorial!

A good starting point to customize your model are settings of the default configuration file `config.default`. You may want to do a reserve copy of your current configuration file and then overwrite it by a default configuration:

.. code:: bash

    .../pypsa-rsa (pypsa-rsa) % cp config.yaml config.default.yaml


How to execute different parts of the workflow?
===============================================

Snakemake is a workflow management tool inherited by PyPSA-RSA from PyPSA-Eur.
Snakemake decomposes a large software process into a set of subtasks, or 'rules', that are automatically chained to obtain the desired output.

.. note::

  ``Snakemake``, which is one of the major dependencies, will be automatically installed in the environment pypsa-rsa, thereby there is no need to install it manually.

The snakemake included in the conda environment pypsa-rsa can be used to execute any custom rule with the following command:

.. code:: bash

    .../pypsa-rsa (pypsa-rsa) % snakemake < your custom rule >

Starting with essential usability features, the implemented PyPSA-RSA `Snakemake procedure <https://github.com/PyPSA/pypsa-za/blob/master/Snakefile>`_ 
allows to flexibly execute the entire workflow with various options without writing a single line of python code. For instance, you can model South Africa's energy system 
using the required data. Wildcards, which are special generic keys that can assume multiple values depending on the configuration options, 
help to execute large workflows with parameter sweeps and various options.

You can execute some parts of the workflow in case you are interested in some specific parts.
E.g. renewable resource potentials for onshore wind in ``redz`` areas for a single node model may be generated with the following command which refers to the script name: 

.. code:: bash

    .../pypsa-rsa (pypsa-rsa) % snakemake -j 1 resources/profile_onwind_1-supply_redz.nc

How to use PyPSA-RSA for your energy problem?
===============================================

PyPSA-RSA mostly relies on :ref:`input datasets <data_workflow>` specific to South Africa but can be tailored to represent any part of the world in a few steps. The following procedure is recommended.

1. Adjust the model configuration
---------------------------------

The main parameters needed to customize the inputs for your national-specific data are defined in the :ref:`configuration <config>` file `config.yaml`. 
The configuration settings should be adjusted according to a particular problem you are intending to model. The main country-dependent parameters are:

* `regions` parameter which defines the network topology;

* `resareas` parameter which defines zones suitable for renewable expansion based on country specific policies;

* `cutouts` and `cutout` parameters which refer to a name of the climate data archive (so called `cutout <https://atlite.readthedocs.io/en/latest/ref_api.html#cutout>`_) to be used for calculation of the renewable potential.

Apart from that, it's important to check that there is a proper match between the temporal and spatial parameters across the configuration file as it is essential to build the model properly. 
Generally, if there are any mysterious error message appearing during the first model run, there are chances that it can be resolved by a simple config check.

It could be helpful to keep in mind the following points:

1. the cutout name should be the same across the whole configuration file (there are several entries, one under `atlite` and some under each of the `renewable` parameters);

2. the country of interest given as a shape file in **GoogleDrive** should be covered by the cutout area;

3. the cutout time dimension, the weather year used for demand modelling and the actual snapshot should match.

2. Build the custom cutout
--------------------------

The cutout is the main concept of climate data management in PyPSA ecosystem introduced in `atlite <https://atlite.readthedocs.io/en/latest/>`_ package. 
The cutout is an archive containing a spatio-temporal subset of one or more topology and weather datasets. Since such datasets are typically global 
and span multiple decades, the Cutout class allows atlite to reduce the scope to a more manageable size. More details about the climate data processing 
concepts are contained in `JOSS paper <https://joss.theoj.org/papers/10.21105/joss.03294>`_.

In case you are interested in other parts of the world you have to generate a cutout yourself using the `build_cutouts` rule. To run it you will need to: 

1. be registered on  the `Copernicus Climate Data Store <https://cds.climate.copernicus.eu>`_;

2. install `cdsapi` package  (can be installed with `pip`);

3. setup your CDS API key as described `on their website <https://cds.climate.copernicus.eu/how-to-api>`_.

Normally cutout extent is calculated from the shape of the requested region defined by the `countries` parameter in the configuration file `config.yaml`. 
It could make sense to set the countries list as big as it's feasible when generating a cutout. A considered area can be narrowed anytime when building 
a specific model by adjusting content of the `countries` list. There is also the option to set the cutout extent specifying `x` and `y` values directly. Please use direct definition of `x` 
and `y` only if you really understand what and why you are doing. 

3. Build a natura.tiff raster
-----------------------------

A raster file `natura.tiff` is used to store shapes of the protected and reserved nature areas. Such landuse restrictions can be taking into account when calculating the 
renewable potential.

.. note::
    Skip this recommendation if the region of your interest is within Africa

.. how to build natura raster?

..
    How to validate?
    ================

    .. TODO add a list of actions needed to do the validation

    To validate the data obtained with PyPSA-Earth, we recommend to go through the procedure here detailed. An exampled of the validation procedure is available in the `Nigeria validation <https://github.com/pypsa-meets-earth/documentation/blob/main/notebooks/validation/validation_nigeria.ipynb>`_ notebook. Public information on the power system of Nigeria are compared to those obtained from the PyPSA-Earth model.

    Simulation procedure
    --------------------

    It may be recommended to check the following quantities the validation:

    #. inputs used by the model:

        #. network characteristics;

        #. substations;

        #. installed generation by type;

    #. outputs of the simulation:

        #. demand;

        #. energy mix.

    Where to look for reference data
    --------------------------------
    
    Data availability for many parts of the world is still quite limited. Usually the best sources to compare with are regional data hubs. There is also a collection of harmonized datasets curated by the international organisations. A non-exhaustive list of helpful sources:

    * `World Bank <https://energydata.info/>`_;

    * International Renewable Energy Agency `IRENA <https://pxweb.irena.org/pxweb/en/IRENASTAT/IRENASTAT__Power%20Capacity%20and%20Generation/ELECCAP_2022_cycle2.px/>`_;

    * International Energy Agency `IEA <https://www.iea.org/data-and-statistics>`_;

    * `BP <https://www.bp.com/en/global/corporate/energy-economics/statistical-review-of-world-energy.html>`_ Statistical Review of World Energy;

    * International Energy Agency `IEA <https://www.iea.org/data-and-statistics>`_;

    * `Ember <https://ember-climate.org/data/data-explorer/>`_ Data Explorer.

..
    Advanced validation examples
    ----------------------------

    The following validation notebooks are worth a look when validating your energy model:

    1. A detailed `network validation <https://github.com/pypsa-meets-earth/documentation/blob/main/notebooks/validation/network_validation.ipynb>`_.
    
    2. Analys of `the installed capacity <https://github.com/pypsa-meets-earth/documentation/blob/main/notebooks/validation/capacity_validation.ipynb>`_ for the considered area. 

    3. Validation of `the power demand <https://github.com/pypsa-meets-earth/documentation/blob/main/notebooks/validation/demand_validation.ipynb>`_ values and profile.

    4. Validation of `hydro <https://github.com/pypsa-meets-earth/documentation/blob/main/notebooks/validation/hydro_generation_validation.ipynb>`_, `solar and wind <https://github.com/pypsa-meets-earth/documentation/blob/main/notebooks/validation/renewable_potential_validation.ipynb>`_ potentials.


.. include:: ./how_to_docs.rst

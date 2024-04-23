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
- step-by-step exucetion of the ``snakemake`` workflow, from network creation through solving the network to analysing the results.

The ``model_file.xlsx`` and ``config.yaml`` files are utilised to customise the PyPSA-RSA model. The tutorial's configuration is contained in 
``model_file_tutorial.xlsx`` and ``config.tutorial.yaml``. Use the configuration and model setup files ``config.yaml`` and ``model_file.xlsx`` to run the tutorial

.. code:: bash

    .../pypsa-za % cp config.tutorial.yaml config.yaml
    .../pypsa-za % cp model_file_tutorial.xlsx model_file.xlsx

..
    This configuration is set to download a reduced data set via the rules :mod:`retrieve_databundle`,
    :mod:`retrieve_natura_raster`, :mod:`retrieve_cutout` totalling at less than 250 MB.
    The full set of data dependencies would consume 5.3 GB.
    For more information on the data dependencies of PyPSA-RSA, continue reading :ref:`data`.

How to customise PyPSA-RSA?
=============================

Model setup: model_file.xlsx
----------------------------

The ``model_file.xlsx`` contains databases of existing conventional and renewable power stations owned by Eskom or by IPP's.  

- existing Eskom stations
    - scenario
        - power station name
        - carrier 
        - carrier type
        - status
        - capacity (MW)
        - unit size (MW)
        - number of units
        - future commissioning date
        - decommissioning date 
        - heat rate (GJ/MWh)
        - fuel price (R/GJ)
        - max ramp up (MW/min)
        - max ramp down (MW/min)
        - min stable level (%)
        - variable O&M cost (R/MWh)
        - fixed O&M cost (R/MWh)
        - pump efficiency (%)
        - pump units
        - pump load per unit (MW)
        - pumped storage - max storage (GWh)
        - csp storage (hours)
        - diesel storage (Ml)
        - gas storage (MCM)
        - GPS latitude
        - GPS longitude
- existing non-Eskom stations
    - scenario
        - same as above, in addition:
        - grouping 
- new build limits
    - scenario
        - minimum installed limit
        - maximum installed limit
- projected parameters
    - scenario
        - demand
        - coal fleet energy availability factor (EAF)
        - spinning reserves
        - total reserves
        - reserve margin
        - active reserve margin
- technology costs
    - scenario
        - discount rate
        - heat rate
        - efficiency
        - fixed O&M
        - variable O&M
        - investment
        - lifetime
        - fuel 
        - CO2 intensity
- model setup
    - wildcard
    - simulation years
    - scenario: existing Eskom stations
    - scenario: existing non-Eskom stations
    - scenario: new build limits
    - scenario: projected parameters
    - scenario: costs


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

PyPSA-RSA provides three methods for generating renewable resource data. The tag ``use_eskom_wind_solar`` uses the pu profiles for all wind and solar generators as obtained from Eskom. The 
tag ``use_excel_wind_solar`` utilises user specific hourly pu profiles provided in an excel spreadsheet. The tag ``build_renewables_profiles`` enables the model to calculate the temporal and 
spatial availability of renewables such as wind and solar energy using historical weather data.

.. literalinclude:: ../config.tutorial.yaml
   :language: yaml
   :start-at: use_eskom_wind_solar:
   :end-at: build_renewable_profiles:

For either three methods historical weather data is used and thus the year in which the data was obtained is specified for each carrier under the tag ``reference_weather_years``.

.. literalinclude:: ../config.tutorial.yaml
   :language: yaml
   :start-at: reference_weather_years:
   :end-before: electricity:

If ``build_renewables_profiles`` is enabled then ``atlite`` is used to generate the renewable resource potential using reanalysis data which can be 
downloaded by enabling the ``build_cutout`` tag. 

.. literalinclude:: ../config.tutorial.yaml
   :language: yaml
   :start-at: build_cutout:
   :end-before: use_eskom_wind_solar:

The cutout is is configured under the ``atlite`` tag. The options below can be adapted to download weather data for the required range of coordinates surrounding South Africa.
For more details on ``atlite`` please follow the `tutorials <https://atlite.readthedocs.io/en/latest/examples/create_cutout.html>`_.

.. literalinclude:: ../config.tutorial.yaml
   :language: yaml
   :start-at: atlite:
   :end-before: renewable:

The spatial resolution of the downloaded ERA5 dataset is given on a 30km x 30km grid. For wind power generation, this spatial resolution is  not enough to resolve the local
dynamics. Enabling the ``apply_wind_correction`` tag, uses global wind atlas mean wind speed at 100m to correct the ERA5 data.

Once the historical weather data is downloaded, ``atlite`` is used to convert the weather data to power systems data. Atlite uses pre-defined or custom turbine properties 
which are specified under the ``resource`` tag.

.. literalinclude:: ../config.tutorial.yaml
   :language: yaml
   :start-after: renewable:
   :end-at: capacity_per_sqkm:

Similarly, solar pv profiles are generated using pre-defined or custom panel properties which are specified under the ``resource`` tag.

.. literalinclude:: ../config.tutorial.yaml
   :language: yaml 
   :lines: 170-177

The renewable potentials are calculated for eligible land, excluding the conservation and protected areas. When the ``natura`` tag is enabled, the SACAD and SAPAD shape files located in `data/bundle` are 
converted into ``tiff`` files. The conservation and protectected areas together with the areas of land with the ``grid_codes`` specified are excluded from calculation of renewable potential. 
 
.. literalinclude:: ../config.tutorial.yaml
   :language: yaml
   :start-at: salandcover:
   :end-at: clip_p_max_pu:

In addition, the expansion of renewable resources is limited to either the ``redz`` regions or areas close to the strategic transmission ``corridors``.

.. literalinclude:: ../config.tutorial.yaml
   :language: yaml
   :lines: 10

The hydro power is obtained directly from Eskom data.

.. literalinclude:: ../config.tutorial.yaml
   :language: yaml
   :start-at: hydro_inflow:
   :end-at: source:

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

    .../pypsa-za (pypsa-za) % cp config.default.yaml config.yaml


How to execute different parts of the workflow?
===============================================

Snakemake is a workflow management tool inherited by PyPSA-RSA from PyPSA-Eur.
Snakemake decomposes a large software process into a set of subtasks, or ’rules’, that are automatically chained to obtain the desired output.

.. note::

  ``Snakemake``, which is one of the major dependencies, will be automatically installed in the environment pypsa-za, thereby there is no need to install it manually.

The snakemake included in the conda environment pypsa-za can be used to execute any custom rule with the following command:

.. code:: bash

    .../pypsa-za (pypsa-za) % snakemake < your custom rule >  

Starting with essential usability features, the implemented PyPSA-RSA `Snakemake procedure <https://github.com/PyPSA/pypsa-za/blob/master/Snakefile>`_ that 
allows to flexibly execute the entire workflow with various options without writing a single line of python code. For instance, you can model South Africa's energy system 
using the required data. Wildcards, which are special generic keys that can assume multiple values depending on the configuration options, 
help to execute large workflows with parameter sweeps and various options.

You can execute some parts of the workflow in case you are interested in some specific it's parts.
E.g. renewable resource potentials for onshore wind in ``redz`` areas for a single node model may be generated with the following command which refers to the script name: 

.. code:: bash

    .../pypsa-earth (pypsa-earth) % snakemake -j 1 resources/profile_onwind_1-supply_redz.nc

How to use PyPSA-RSA for your energy problem?
===============================================

PyPSA-RSA mostly relies on :ref:`input datasets <data_workflow>` specific to South Africa but can be tailored to represent any part of the world in a few steps. The following procedure is recommended.

1. Adjust the model configuration
---------------------------------

The main parameters needed to customize the inputs for your national-specific data are defined in the :ref:`configuration <config>` file `config.yaml`. 
The configuration settings should be adjusted according to a particular problem you are intending to model. The main country-dependent parameters are:

* `regions` parameter which defines the network topology;

* `resareas` parameter which defines zones suitable for renewable expansion based on country specific policies;

* `cutouts` and `cutout` parameters which refer to a name of the climate data archive (so called `cutout <https://atlite.readthedocs.io/en/latest/ref_api.html#cutout>`_) 
to be used for calculation of the renewable potential.

Apart of that, it's worth to check that there is a proper match between the temporal and spatial parameters across the configuration file as it is essential to build the model properly. 
Generally, if there are any mysterious error message appearing during the first model run, there are chances that it can be resolved by a simple config check.

It could be helpful to keep in mind the following points:

1. the cutout name should be the same across the whole configuration file (there are several entries, one under `atlite` and some under each of the `renewable` parameters);

2. the country of interest given as a shape file in `data/supply_regions/` should be covered by the cutout area;

3. the cutout time dimension, the weather year used for demand modelling and the actual snapshot should match.

2. Build the custom cutout
--------------------------

The cutout is the main concept of climate data management in PyPSA ecosystem introduced in `atlite <https://atlite.readthedocs.io/en/latest/>`_ package. 
The cutout is an archive containing a spatio-temporal subset of one or more topology and weather datasets. Since such datasets are typically global 
and span multiple decades, the Cutout class allows atlite to reduce the scope to a more manageable size. More details about the climate data processing 
concepts are contained in `JOSS paper <https://joss.theoj.org/papers/10.21105/joss.03294>`_.

The pre-built cutout for South Africa is available for 2012 year and can be loaded directly from zenodo through the rule `retrieve_cutout`. 

In case you are interested in other parts of the world you have to generate a cutout yourself using the `build_cutouts` rule. To run it you will need to: 

1. be registered on  the `Copernicus Climate Data Store <https://cds.climate.copernicus.eu>`_;

2. install `cdsapi` package  (can be installed with `pip`);

3. setup your CDS API key as described `on their website <https://cds.climate.copernicus.eu/api-how-to>`_.

These steps are required to use CDS API which allows an automatic file download while executing `build_cutouts` rule.

Normally cutout extent is calculated from the shape of the requested region defined by the `countries` parameter in the configuration file `config.yaml`. 
It could make sense to set the countries list as big as it's feasible when generating a cutout. A considered area can be narrowed anytime when building 
a specific model by adjusting content of the `countries` list.

There is also option to set the cutout extent specifying `x` and `y` values directly. However, these values will overwrite values extracted from the countries 
shape. Which means that nothing prevents `build_cutout` to extract data which has no relation to the requested countries. Please use direct definition of `x` 
and `y` only if you really understand what and why you are doing.

The `build_cutout` flag should be set `true` to generate the cutout. After the cutout is ready, it's recommended to set `build_cutout` to `false` to avoid overwriting the existing cutout by accident.

3. Build a natura.tiff raster
-----------------------------

A raster file `natura.tiff` is used to store shapes of the protected and reserved nature areas. Such landuse restrictions can be taking into account when calculating the 
renewable potential with `build_renewable_profiles`.

.. note::
    Skip this recommendation if the region of your interest is within Africa

A pre-built `natura.tiff` is loaded along with other data needed to run a model with `retrieve_databundle_light` rule. Currently this raster is valid for Africa, 
global `natura.tiff` raster is under development. You may generate the `natura.tiff` for a region of interest using `build_natura_raster` rule which aggregates 
data on protected areas along the cutout extent.

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

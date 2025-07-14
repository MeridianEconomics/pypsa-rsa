..
  SPDX-FileCopyrightText: 2021 The PyPSA-ZA authors

  SPDX-License-Identifier: CC-BY-4.0

.. _introduction:

##########################################
Introduction
##########################################

History
========

Historically, commercial tools have been extensively utilised by both governments and utilities to develop capacity 
expansion plans. One of the challenges of utilising such commercial tools to support transparent energy policy is 
the closed nature of the models, which are essentially a ‘black-box’. For this reason, initiatives such as 
`openmod <https://openmod-initiative.org/manifesto.html>`_ have been developed with the goal of creating open energy 
modelling platforms that are based on `open-source software <https://opensource.com/resources/what-open-source>`_ as well as open data.

`PyPSA (Python for Power System Analysis) <https://pypsa.org/>`_ is a diverse toolbox of modules that can be assembled to produce a power / energy 
system modelling framework to meet specific requirements. As described by `Brown et al <https://arxiv.org/pdf/1707.09913.pdf>`_ 
PyPSA includes a toolbox of modules for multi-horizon investment planning, unit commitment of conventional generators, 
time-varying renewable generators, storage units, all combinations of direct and alternating current electricity networks, 
and the coupling of electricity to other energy sectors, such as gas, heating and transport.

An initial PyPSA-ZA model of the South African power system called PyPSA-ZA was developed by `Hörsch 
and Calitz <https://arxiv.org/pdf/1710.11199.pdf>`_ who studied a future renewable energy based 
South African electricity network for a single reference year (in other words, it was not an 
intertemporally optimised power system development model – showing an optimised transition 
over time – it only provided a snapshot of an optimised high renewables system at a particular 
point in time – 2040, albeit with reasonable spatial (network) resolution. The initial PyPSA-ZA model 
is described and evaluated in the paper 
`PyPSA-ZA: Investment and operation co-optimization of integrating wind and solar in South Africa at high spatial and temporal detail <https://arxiv.org/abs/1710.11199>`_.

`PyPSA <https://pypsa.org/>`_ has also been utilised to build an extensive interconnected power system model of the European power system 
`PyPSA-Eur <https://github.com/PyPSA/pypsa-eur>`_. This model has been further extended to include full sector 
coupling of power, transportation and heat demand `PyPSA-Eur-Sec <https://github.com/PyPSA/pypsa-eur-sec>`_. 
Like the PyPSA-ZA model, the PyPSA-Eur model framework has been designed to handle high spatial resolution 
(detailed network specification, i.e. grid constraints), but it does not perform multi-year optimisation and instead analyses 
a single year snapshot. In the African context the project `PyPSA-Meets-Earth <https://github.com/pypsa-meets-earth/pypsa-earth>`_ 
is currently ongoing and aims to develop an interconnected, high-level, African power system model on the same basis as PyPSA-Eur. 
Multi-horizon planning functionality, based on the assumption of “perfect foresight”, was only recently added to PyPSA in 2021, 
and currently PyPSA-Eur, PyPSA-ZA, and PyPSA-meets-Earth do not yet incorporate this capability. 
The PyPSA-Eur-Sec model does include myopic expansion planning capability that is based on separating the planning horizon 
into several shorter periods that are solved as separate optimisations.

Currently, there is no existing `PyPSA <https://pypsa.org/>`_ model 
for South Africa that can be used to address the multi-year, least cost optimised, transition path to 
a decarbonised power sector over the coming decades. Therefore, an updated and expanded PyPSA-ZA model 
is required that is comparable with the existing, mostly proprietary, modelling approaches that are 
used in the country. 

In 2022 `Meridian Economics <https://meridianeconomics.co.za/>`_ leveraged the experience from 
`PyPSA-Eur <https://github.com/PyPSA/pypsa-eur>`_, PyPSA-ZA and `PyPSA meets Earth <https://pypsa-meets-earth.github.io/>`_ 
to significantly shorten the development time frame for the new model. `Meridian Economics <https://meridianeconomics.co.za/>`_ has undergone extensive work in terms of 
input datasets, model development, and validation against existing results from commercial tools to establish PyPSA-ZA credibility to ensure that the outcomes can 
be trusted to support policy decisions. The updated PyPSA-ZA model has the following capabilities:

- ``Updated input data``: Includes latest assumptions around technology performance and learning rates.
- ``Multi-horizon capacity expansion planning``: Myopic and/or perfect foresight expansion capabilities which can solve in five-year time steps up to 2050.
- ``Validation test case`` : Benchmarked case studies using existing energy planning results in the public domain such as those developed by the `CSIR <https://www.csir.co.za/>`_.
- ``Solar and wind resource assessment`` : Utilises the `Atlite module <https://github.com/PyPSA/atlite>`_ and `Global Wind Atlas <https://globalwindatlas.info/en>`_ to generate renewable potentials for South Africa’s Renewable Energy Development Zones.
- ``Model file`` : Input excel spreadsheet for easier model input.

Folder structure
================

The content in this package is organized in folders as described below; for more details, please see the documentation.

- ``data``: Includes input data that is not produced by any ``snakemake`` rule.
- ``scripts``: Includes all the Python scripts executed by the ``snakemake`` rules.
- ``resources``: Stores intermediate results of the workflow which can be picked up again by subsequent rules.
- ``networks``: Stores intermediate, unsolved stages of the PyPSA network that describes the energy system model.
- ``results``: Stores the solved PyPSA network data, summary files and plots.
- ``benchmarks``: Stores ``snakemake`` benchmarks.
- ``logs``: Stores log files about solving, including the solver output, console output and the output of a memory logger.
- ``envs``: Stores the conda environment files to successfully run the workflow.


License
=======

PyPSA-ZA work is released under multiple licenses:

* All original source code is licensed as free software under `MIT License <https://github.com/PyPSA/pypsa-eur/blob/master/LICENSES/MIT.txt>`_.
* The documentation is licensed under `CC-BY-4.0 <https://creativecommons.org/licenses/by/4.0/>`_.
* Configuration files are mostly licensed under `CC0-1.0 <https://creativecommons.org/publicdomain/zero/1.0/>`_.
* Data files are licensed under different licenses as noted below.

Licenses and urls of the data used in PyPSA-ZA:

.. csv-table::
   :header-rows: 1
   :file: configtables/licenses.csv


* *BY: Attribute Source*
* *NC: Non-Commercial Use Only*
* *SA: Share Alike*

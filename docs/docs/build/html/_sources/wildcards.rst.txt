..
  SPDX-FileCopyrightText: 2019-2020 The PyPSA-Eur Authors

  SPDX-License-Identifier: CC-BY-4.0

.. _wildcards:

#########
Wildcards
#########

It is easy to run PyPSA-RSA for multiple scenarios using the wildcards feature of ``snakemake``.
Wildcards allow to generalise a rule to produce all files that follow a regular expression pattern
which e.g. defines one particular scenario. One can think of a wildcard as a parameter that shows
up in the input/output file names of the ``Snakefile`` and thereby determines which rules to run,
what data to retrieve and what files to produce.

Detailed explanations of how wildcards work in ``snakemake`` can be found in the
`relevant section of the documentation <https://snakemake.readthedocs.io/en/stable/snakefiles/rules.html#wildcards>`_.

.. _resarea:

The ``{resarea}`` wildcard
==========================

The ``{resarea}`` wildcard specifies whether to contrain renewable generators to `Renewable Energy Development Zones <https://egis.environment.gov.za/redz>`_  or strategic transmission corridors.

.. csv-table::
   :header-rows: 1
   :widths: 10,20,10,10
   :file: configtables/resarea.csv

.. _model_file:

The ``{model_file}`` wildcard
=============================

The ``{model_file}`` wildcard specifies the technology cost assumptions to use from the input spreadsheet.  
Cost assumptions in model_file.xlsx

.. csv-table::
   :header-rows: 1
   :widths: 10,20,10,10
   :file: configtables/model_setup.csv

.. _ll:

The ``{ll}`` wildcard
=====================

The ``{ll}`` wildcard specifies what limits on
line expansion are set for the optimisation model.
It is handled in the rule :mod:`prepare_network`.

The wildcard, in general, consists of two parts:

    1. The first part can be
       ``v`` (for setting a limit on line volume) or
       ``c`` (for setting a limit on line cost)

    2. The second part can be
       ``opt`` or a float bigger than one (e.g. 1.25).

       (a) If ``opt`` is chosen line expansion is optimised
           according to its capital cost
           (where the choice ``v`` only considers overhead costs for HVDC transmission lines, while
           ``c`` uses more accurate costs distinguishing between
           overhead and underwater sections and including inverter pairs).

       (b) ``v1.25`` will limit the total volume of line expansion
           to 25 % of currently installed capacities weighted by
           individual line lengths; investment costs are neglected.

       (c) ``c1.25`` will allow to build a transmission network that
           costs no more than 25 % more than the current system.

.. _opts:

The ``{opts}`` wildcard
=======================

The ``{opts}`` wildcard triggers optional constraints, which are activated in either
:mod:`prepare_network` or the :mod:`solve_network` step.
It may hold multiple triggers separated by ``-``, i.e. ``Co2L-3H`` contains the
``Co2L`` trigger and the ``3H`` switch. There are currently:


.. csv-table::
   :header-rows: 1
   :widths: 10,20,10,10
   :file: configtables/opts.csv

.. _regions:

The ``{regions}`` wildcard
==========================

The PyPSA-RSA models can be narrowed to various number of nodes using the ``{regions}`` wildcard.

If ``regions=27-supply``, then the rule :mod:`build_topology` setups 27 nodes  based on the shape files. 
If otherwise ``regions=RSA``, ``regions=9-supply``, ``regions=10-supply``, then the network is narrowed to n number of buses for the South African network. 


.. _cutout_wc:

The ``{cutout}`` wildcard
=========================

The ``{cutout}`` wildcard facilitates running the rule :mod:`build_cutout`
for all cutout configurations specified under ``atlite: cutouts:``.
These cutouts will be stored in a folder specified by ``{cutout}``.

.. _technology:

The ``{technology}`` wildcard
=============================

The ``{technology}`` wildcard specifies for which renewable energy technology to produce availability time
series and potentials using the rule :mod:`build_renewable_profiles`.
It can take the values ``onwind`` and ``solar`` but **not** ``hydro``
(since hydroelectric plant profiles are created by a different rule).

The wildcard can moreover be used to create technology specific figures and summaries.
For instance ``{technology}`` can be used to plot regionally disaggregated potentials
with the rule :mod:`plot_p_nom_max`.

.. _attr:

The ``{attr}`` wildcard
=======================

The ``{attr}`` wildcard specifies which attribute is used for size
representations of network components on a map plot produced by the rule
:mod:`plot_network`. While it might be extended in the future, ``{attr}``
currently only supports plotting of ``p_nom``.

.. _ext:

The ``{ext}`` wildcard
======================

The ``{ext}`` wildcard specifies the file type of the figures the
rule :mod:`plot_network` produce.
Typical examples are ``pdf`` and ``png``. The list of supported file
formats depends on the used backend. To query the supported file types on your system, issue:

.. code:: python

    import matplotlib.pyplot as plt
    plt.gcf().canvas.get_supported_filetypes()

# SPDX-FileCopyrightText:  PyPSA-RSA Authors
# SPDX-License-Identifier: MIT

.. _known_issues:

===========================
Known Implementation Issues
===========================

This page records implementation defects found by code inspection of the
``scripts/`` and ``pre_processing/`` trees, with the evidence used to confirm
each one. Every entry states whether the defect is **live** (it changes the
results of a scenario that currently ships) or **latent** (it sits on a branch
no current scenario activates, so it has never executed).

Line references are against commit ``89872c1``.

Severity ordering below reflects the size of the outcome anomaly, not the
difficulty of the fix.

.. contents::
   :local:
   :depth: 1

----

Critical
========

Carbon constraint is never constructed
--------------------------------------

**Location** ``scripts/custom_constraints.py:332-336``

.. code:: python

    conv = 1
    if annual_limits.unit.split("/")[0] == "Mt":     # column is named "units"
        conv = 1e9 # convert to kgCO2
    elif annual_limits.units.split("/")[0] == "Gt":  # would NameError if reached
        conv = 1e12 # convert to kgCO2

``annual_limits`` is a ``pandas.Series``. The unit column is ``units``, so
``annual_limits.unit`` raises ``AttributeError`` before any constraint is
added, and the ``elif`` names a different non-existent attribute again.

**Evidence** ``d.unit`` raises ``AttributeError: 'Series' object has no
attribute 'unit'``; ``d.units`` returns ``'Mt/yr'``.

The downstream ``rhs = (annual_limits.loc[y] * conv)`` is **not** a second
defect: ``.loc[y]`` selects a single element from the Series, so the product is
a ``float64`` scalar once ``conv`` is correct. Only the attribute name blocks
the constraint.

**Status** Latent. ``carbon_constraints`` is ``none`` for every row of
``scenarios/Coal_Flexibilisation/scenarios_to_run.xlsx``, so
``add_annual_co2_constraints`` has never been reached. There is no ``try``
around ``solve_network``, so the first scenario that selects a carbon
constraint will abort the rule rather than silently degrade.

Call-site defects on the carbon path
------------------------------------

**Location** ``scripts/prepare_and_solve_network.py:460-461``

.. code:: python

    gen_emissions = pd.read_csv(snakemake.input["generator_emissioans"],index_col=[0])
    add_annual_co2_constraints(n, sns, param, SCENARIO_SETUP, gen_emissions)

Two independent blockers:

1. The Snakefile input key is ``generator_emissions`` (``Snakefile:93``); the
   typo ``generator_emissioans`` raises ``KeyError``.
2. The signature is
   ``add_annual_co2_constraints(n, sns, scenario_setup, gen_emissions)``
   (``custom_constraints.py:325``). ``param`` — the extendable-parameter
   DataFrame — is passed where ``scenario_setup`` is expected, so
   ``scenario_setup["sub_path"]`` fails even once the key is corrected.

**Status** Latent, behind the same ``carbon_constraints`` gate as above.

Global extendable capacity limits are never applied
---------------------------------------------------

**Location** ``scripts/prepare_and_solve_network.py:97``

.. code:: python

    ).loc[(SCENARIO_SETUP[f"extendable_{lim}_total"], "global", slice(None), slice(None)), ext_years]

The lookup hard-codes the literal level value ``"global"``. The workbook
stores ``location = 'RSA'`` and ``supply_region = '1'`` — no row carries
``"global"``.

**Evidence** ``location`` unique values are ``['RSA']``; ``supply_region``
unique values are ``['1']``. The ``.loc`` raises, the bare
``except Exception`` at line 100 logs a warning, and the function returns
without adding a single ``GlobalConstraint``.

**Impact** The carrier-level cap is never built. The call site guards this with
``if len(n.buses) != 1: #covered under single bus limits``
(``prepare_and_solve_network.py:526``), and for the shipped single-bus case that
reasoning holds: ``set_extendable_limits_per_bus`` writes
``nom_max_{carrier}_{year}`` onto the one ``RSA`` bus, which PyPSA turns into
the same cap (``define_nominal_constraints_per_bus_carrier``). So the guard is
right about the 1-bus case and the defect is confined to multi-bus runs, where
the two paths are no longer equivalent and the carrier cap silently disappears
while ``extendable_max_total`` still names a scenario key.

**Status** Latent for the shipped scenario set — every row sets ``regions = 1``,
so ``len(n.buses) != 1`` is false and the function is never called
(``prepare_and_solve_network.py:526``). Live on the first multi-bus run, where
``extendable_max_total`` is set to a real scenario key and the modeller expects
a national cap that was never built.

High
====

Phantom buses created by per-bus limit assignment
-------------------------------------------------

**Location** ``scripts/prepare_and_solve_network.py:156``

.. code:: python

    n.buses.loc[idx[0],f"nom_{lim}_{idx[1]}_{y}"] = bus_limit.loc[idx, y]

After ``bus_limit.index.droplevel([0, 1, 2])``, ``idx`` is
``(location, carrier)``. Assigning to a missing index label in pandas
**creates a new row** rather than raising.

**Evidence** a two-row frame grew to three rows with all other columns ``NaN``
when a non-existent label was assigned.

**Impact** If the ``location`` label does not match a bus name, phantom buses
appear in the network and change its size, silently. For the shipped 1-bus
``RSA`` case this works only because ``location`` happens to equal ``RSA``; the
defect is dormant rather than absent.

**Status** Latent for the current 1-bus scenarios; live the moment a multi-bus
scenario has a location label that is not a bus name.

Segmentation never writes back to the network
---------------------------------------------

**Location** ``scripts/_helpers.py:777-781``

.. code:: python

    def assign_segmented_df_to_network(df, search_str, replace_str, target):
        cols = df.columns[df.columns.str.contains(search_str)]
        segmented = df[cols]
        segmented.columns = segmented.columns.str.replace(search_str, replace_str)
        target = segmented

``target = segmented`` rebinds a local name. The caller's frame is untouched.

**Evidence** passing a populated frame as ``target`` leaves it unchanged.

**Impact** ``add_electricity.py:1603-1606`` calls this four times for
``p_max_pu``, ``p_min_pu``, load and inflow. None of them are written, while
``n.set_snapshots(segmented_df.index)`` at line 1600 *has* already shrunk the
time axis. The network therefore carries an 8760-row time series against a
segmented index, and ``set_snapshots`` reindexes the stale data onto the new
axis, filling with attribute defaults. Temporal segmentation silently discards
the underlying profiles.

**Status** Live whenever a scenario's ``options`` contains an ``NSEG`` token.

De-normalisation multiplier is always 1.0
-----------------------------------------

**Location** ``scripts/_helpers.py:764-769``

.. code:: python

    def normalize_and_rename_df(df, snapshots, fillna, suffix=None):
        df = df.loc[snapshots]
        df = (df / df.max()).fillna(fillna)
        if suffix:
            df.columns += f'_{suffix}'
        return df, df.max()

The returned maximum is computed **after** division, so it is 1.0 for every
column instead of the original peak.

**Evidence** for input maxima ``[4800., 10.]`` the returned Series is
``[1.0, 1.0]``.

**Impact** ``single_year_segmentation`` (``add_electricity.py:1529-1531``)
uses these values to restore absolute magnitude at lines 1565-1567. Load and
inflow are therefore multiplied by 1.0 and the segments carry normalised
values, not MW. This compounds with the previous entry: even if the write-back
worked, the magnitudes would be wrong.

**Status** Live on the same ``NSEG`` path.

Medium
======

Ramp-down clean-up tests and writes the wrong attribute
-------------------------------------------------------

**Location** ``scripts/add_electricity.py:1434`` and ``1439``

.. code:: python

    errors = ramp_limit_start_up < p_min_pu        # should be ramp_limit_shut_down
    ...
    n.generators_t.ramp_limit_shut_down = ramp_limit_shut_down.where(~errors, p_min_pu)

Two faults in one branch: the predicate tests ``ramp_limit_start_up`` where the
branch is about shut-down, and the write target is ``ramp_limit_shut_down``
instead of ``ramp_limit_down``.

**Evidence** the predicate selects the wrong generators.

**Impact** None, and that is the defect. ``ramp_limit_start_up`` and
``ramp_limit_shut_down`` are declared ``float`` — static, not ``static or
series`` — in ``pypsa/data/component_attrs/generators.csv``. Assigning
``n.generators_t.ramp_limit_shut_down`` therefore writes a dynamic attribute
that nothing reads: the committable shut-down constraint takes
``n.static(c).ramp_limit_shut_down[com_i]``
(``pypsa/optimization/constraints.py:168``). The clean-up is inert on both the
start-up and shut-down branch, so the infeasibility it exists to prevent is not
prevented. The naming fault above is real but second-order.

**Status** Live on every shipped run — ``clean_pu_profiles: true`` in
``config.yaml`` and ``unit_committment = True`` for all Coal_Flexibilisation
scenarios, so the ``(n.generators.committable == True).any()`` guard passes and
both branches execute.

Ramp-down fallback writes shut-down limits
------------------------------------------

**Location** ``scripts/add_electricity.py:1458``

.. code:: python

    errors = ramp_limit_down < 0.05
    ...
    n.generators_t.ramp_limit_shut_down = ramp_limit_down.where(~errors, 0.05)

The predicate is right here, but the assignment targets ``ramp_limit_shut_down``
rather than ``ramp_limit_down``, so the clamp is written to the wrong place and
``ramp_limit_down`` keeps its sub-5% values. Unlike the block above, this one
*would* work if it named ``ramp_limit_down``: that attribute is ``static or
series``, so a per-hour value is meaningful and
``pypsa/optimization/constraints.py:406`` reads it through
``get_as_dense``. This is the one ramp fault whose fix has an effect.

**Status** Live under the same ``clean_pu_profiles`` flag.

Low
===

Duplicate ``single_year_network_copy`` definition
-------------------------------------------------

**Location** ``scripts/_helpers.py:949`` and ``1033``

Two byte-identical definitions; the second shadows the first. Harmless at
runtime, but the file carries a dead copy and a misleading comment
("Not needed once PyPSA main is updated") above the live one.

Dead coal start-up limit
------------------------

**Location** ``scripts/custom_constraints.py:234``

``limit_coal_start_ups_capacity`` is defined and referenced nowhere. The
``full_outages_pu_max`` parameter threaded through
``add_coal_decom(n, start_limits, full_outages_pu_max)`` is likewise unused
inside that function's body — it appears only in the signature, and the caller
passes an empty ``pd.DataFrame()`` (``prepare_and_solve_network.py:532``). The
coal start-up limit is therefore dead code.

``min_ramp_limit_threshold`` is never read
------------------------------------------

**Location** ``config.yaml:137``

The setting exists but ``check_pu_profiles`` hard-codes ``0.05`` four times —
twice as a predicate and twice as the replacement value, at
``add_electricity.py:1444``, ``1449``, ``1453`` and ``1458``. Editing the config
value has no effect.

``check_pu_profiles`` runs twice
--------------------------------

**Location** ``scripts/add_electricity.py:1727`` and
``scripts/prepare_and_solve_network.py:534``

The second call is redundant for the clip — the first has already run before
``elec.nc`` was written — but it is not a no-op. It re-applies the ramp
clean-up to a network that has just been reloaded from disk, and since the
clean-up writes an attribute PyPSA does not read, neither call changes the
model. The two calls are best understood as one intention written twice, and
the fix belongs in whichever survives.

Latent: fleet-wide ramp start-up override
=========================================

**Location** ``scripts/prepare_and_solve_network.py:515-517``

.. code:: python

    n.generators.ramp_limit_start_up = 0.65
    n.generators.ramp_limit_shut_down = 0.65

This overwrites the whole fleet after the network is loaded from disk,
discarding the per-station values the workbook supplies.

This is **not currently a defect**, but the margin is narrower than it looks.
PyPSA reads these two attributes only for committable units
(``pypsa/optimization/constraints.py:167-168``). In the shipped configuration
that set is *fixed* coal only: ``fixed_technologies.xlsx`` sets
``dispatch_committable = True`` on the 109 coal rows and ``False`` everywhere
else, and ``add_electricity.py:1154`` forces ``committable = False`` on the
entire extendable fleet. Those fixed coal rows already carry ``0.65``, so the
assignment is a no-op.

Two things it nonetheless destroys:

- The *extendable* coal rows in ``extendable_technologies.xlsx`` specify
  ``ramp_limit_start_up = ramp_limit_shut_down = 0.25``, and ``nuclear``
  ``0.05``. Those values reach the network through
  ``set_extendable_params`` and are then overwritten for every generator in the
  frame, committable or not. They are inert only because no extendable
  generator is committable today. ``config.yaml:141`` defines
  ``extendable_committable_carriers_for_dispatch: {extended: [coal, nuclear,
  ccgt_steam]}``, which is referenced nowhere in ``scripts/`` — wiring it up, as
  its name invites, turns this line into a live defect that silently quadruples
  the modelled start-up ramp of every new coal unit.
- ``sasol_coal``, ``sasol_gas``, ``hydro`` and ``ocgt_diesel`` are fixed at
  ``1.0`` in the workbook. The override writes ``0.65`` over all of them.

The general point: a fleet-wide write after ``Network()`` load cannot be
reconciled with a per-station workbook, and the fact that it is currently
inert depends on a config key that nothing reads.

----

Checked and found correct
=========================

These were suspected during the review and cleared, recorded so they are not
re-investigated:

- ``phased_decom.loc[y, gen]`` — the schedule index is ``float64`` and both
  ``int`` and ``float`` keys resolve. No ``KeyError``.
- ``get_active_assets`` returns a ``Series``, and all three call sites use
  boolean indexing (``index[mask]``), which is correct.
- ``check_active``'s ``list.intersection(active)`` — ``active`` is an ``Index``
  and ``list`` is an ``Index``, so ``Index.intersection`` returns an ``Index``.
  The shadowed builtin is harmless here.
- ``scenario_setup["sub_path"]`` inside ``add_annual_co2_constraints`` is
  correct; it is the *call site* that passes the wrong object.
- ``load_disaggregate`` is properly imported at ``add_electricity.py:174``.
- ``build_line_topology``'s ``.apply(Point)`` over coordinate tuples works, and
  the ``not in regions.index`` guard at line 118 precedes the unguarded ``bus0``
  assignment at line 124.
- ``add_coal_decom``'s ``min_phased_decom.loc[y, gen]`` resolves for every
  investment period in the shipped ``DELAYED_ESKOM_2035`` schedule.

----

Running on open-source solvers
==============================

The suite must run end to end without a commercial licence. Three things stood
in the way, all now addressed.

The ``solver`` column is optional
---------------------------------

``scenarios/Coal_Flexibilisation/scenarios_to_run.xlsx`` carries a ``solver``
column; ``scenarios/ME IRP 2024/scenarios_to_run.xlsx`` does not. The old
resolution was a bare double lookup:

.. code:: python

    SOLVER_NAME = snakemake.config["solver"][SCENARIO_SETUP["solver"]].pop("name")

which raises ``KeyError`` for a workbook with no such column. A scenario set
that predates the column could therefore never run, and the failure named
neither the solver nor the valid options. Resolution now falls back to
``config["solver"]["default"]`` when the column is absent or blank, and an
unknown name raises with the list of configured solvers.

``glpk``, ``scip`` and ``cbc`` were missing from ``config.yaml``
----------------------------------------------------------------

Only ``copt``, ``mindopt`` and ``highs`` had blocks, so any scenario naming an
open-source solver other than HiGHS raised ``KeyError`` at the lookup above.
Blocks for ``cbc``, ``glpk`` and ``scip`` are now defined alongside ``highs``,
and ``default`` is ``highs`` — it runs in-process through ``highspy`` and needs
no executable, unlike the three command-line solvers. Note that ``glpk``'s
binary is ``glpsol``: linopy resolves the solver by the name ``glpk`` and then
looks for ``glpsol`` on ``PATH``.

``solver.options.clip_p_max_pu`` was dead
-----------------------------------------

The key appeared in ``config.yaml`` and in the upstream documentation's config
tables, but nothing in ``scripts/``, ``pre_processing/`` or the ``Snakefile``
read it, and PyPSA 0.35.2 has no such setting. It has been removed rather than
left as a knob that appears to do something.

The two scenario workbooks use different schemas
------------------------------------------------

``load_scenario_setup`` hard-coded ``index_col=[1]``. In
``Coal_Flexibilisation`` that consumes the ``scenario`` column; in ``ME IRP
2024`` it consumes ``run_scenario``, which then vanishes as a column. The
identifier is now resolved by name (``scenario`` or the legacy ``wildcard``).

Three further legacy differences are handled at load time:

- ``co2_constraints`` and ``extendable_techs`` are the legacy spellings of
  ``carbon_constraints`` and ``extendable_active``; they are renamed on load.
- Fields introduced after the legacy schema default to their "feature off"
  value. Only fields whose absence unambiguously means the feature was not part
  of that study carry a default. A genuinely missing physical input — weather
  years, fuel prices, emissions — still raises, because inventing a value there
  would change results silently.
- ``capacity_expansion_years`` is read by ``build_topology`` but appears in no
  shipped workbook, so it was a guaranteed ``KeyError`` on the multi-region
  path. It is now derived from the first year of ``simulation_years``.

The ``Snakefile`` had the same problem in a different form: it selected
``scenarios["scenario"]`` by name and filtered ``run_scenario == 1``, so a
legacy workbook produced an empty frame and then ``IndexError: index 0 is out of
bounds``. It now resolves the identifier from either the column or the index and
raises an explicit error when no row is enabled.

What is still missing
---------------------

ME IRP 2024 loads, but it cannot yet run to completion, and the gap is data
rather than code. Its ``sub_scenarios/`` directory predates the current layout:
it has no ``emissions.xlsx``, ``fuel_prices.xlsx``, ``weather.xlsx``,
``phased_decommissioning.xlsx`` or ``aux_stg_feed.xlsx``, and those files exist
nowhere else in the repository. The physical quantities they carry are partly
present in the legacy layout — ``fixed_technologies.xlsx`` still holds ``Fuel
Price (R/GJ)`` and ``CO2 Emissions (kgCO2/MWh)`` columns per station, and
``plant_availability.xlsx`` carries a ``min_station_hrly_cap_fact`` sheet — but
extracting them is a data migration that has to be checked against the source
study, not a schema fix. Weather-year mapping has no legacy equivalent at all.

So the honest status is: the *code* is backward compatible and the loader
accepts both schemas; ME IRP 2024 needs its input workbooks rebuilt before a
run will produce results.

----

A note on why these survived
============================

Every row of ``scenarios/Coal_Flexibilisation/scenarios_to_run.xlsx`` sets
``regions = 1``, ``carbon_constraints = 'none'`` and ``options = 'LC'``. Those
three values close off the per-bus limit path, the whole carbon path, and the
temporal-segmentation path respectively, so none of the Critical or High
findings above can fire on the scenario set that currently ships. They are
latent rather than absent, and they activate on the first multi-bus run, the
first carbon-constrained run, and the first ``NSEG`` run.

What *is* live today is the ramp clean-up in ``check_pu_profiles``. The shipped
Coal_Flexibilisation scenarios set ``unit_committment = True`` and
``config.yaml`` sets ``clean_pu_profiles: true``, so both shut-down branches
execute on every run. They write to ``n.generators_t.ramp_limit_shut_down``,
which nothing reads: the committable shut-down constraint takes the *static*
``n.generators.ramp_limit_shut_down``
(``pypsa/optimization/constraints.py:168``). The clean-up is inert, so the
infeasibility it exists to prevent is not prevented.

The general shape worth guarding against: a bare ``except Exception`` that logs a
warning and returns (as in the global-limits lookup) converts a hard failure into
a silently absent constraint. Where a constraint is the reason the function
exists, absence should be fatal.

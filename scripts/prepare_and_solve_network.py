# SPDX-FileCopyrightText:  PyPSA-ZA2, PyPSA-ZA, PyPSA-Earth and PyPSA-Eur Authors
# # SPDX-License-Identifier: MIT
# coding: utf-8
"""
Prepare PyPSA network for solving according to :ref:`opts` and :ref:`ll`, such as

- adding an annual **limit** of carbon-dioxide emissions,
- adding an exogenous **price** per tonne emissions of carbon-dioxide (or other kinds),
- setting an **N-1 security margin** factor for transmission line capacities,
- specifying an expansion limit on the **cost** of transmission expansion,
- specifying an expansion limit on the **volume** of transmission expansion, and
- reducing the **temporal** resolution by averaging over multiple hours
  or segmenting time series into chunks of varying lengths using ``tsam``.

Relevant Settings
-----------------

.. code:: yaml

    costs:
        emission_prices:
        USD2013_to_EUR2013:
        discountrate:
        marginal_cost:
        capital_cost:

    electricity:
        co2limit:
        max_hours:

.. seealso::
    Documentation of the configuration file ``config.yaml`` at
    :ref:`costs_cf`, :ref:`electricity_cf`

Inputs
------

- ``data/costs.csv``: The database of cost assumptions for all included technologies for specific years from various sources; e.g. discount rate, lifetime, investment (CAPEX), fixed operation and maintenance (FOM), variable operation and maintenance (VOM), fuel costs, efficiency, carbon-dioxide intensity.
- ``networks/elec_s{simpl}_{clusters}.nc``: confer :ref:`cluster`

Outputs
-------

- ``networks/elec_s{simpl}_{clusters}_ec_l{ll}_{opts}.nc``: Complete PyPSA network that will be handed to the ``solve_network`` rule.

Description
-----------

.. tip::
    The rule :mod:`prepare_all_networks` runs
    for all ``scenario`` s in the configuration file
    the rule :mod:`prepare_network`.

"""
import logging
import re

from linopy import LinearExpression, Variable, merge
import numpy as np
import pandas as pd
import pypsa
from pypsa.descriptors import get_switchable_as_dense as get_as_dense, expand_series, get_activity_mask
from pypsa.optimization.common import reindex

from _helpers import configure_logging, remove_leap_day, normalize_and_rename_df, assign_segmented_df_to_network, load_scenario_definition
from add_electricity import load_extendable_parameters, apply_time_segmentation#, update_transmission_costs
import xarray as xr
import warnings
warnings.simplefilter(action='ignore', category=FutureWarning) # Comment out for debugging and development
from custom_constraints import set_operational_limits, ccgt_steam_constraints, reserve_margin_constraints, add_annual_co2_constraints
idx = pd.IndexSlice
import os
from add_electricity import check_pu_profiles

from xarray import DataArray
"""
********************************************************************************
    Build limit constraints
********************************************************************************
"""
def set_extendable_limits_global(n):

    ext_years = n.investment_periods if n.multi_invest else [n.snapshots[0].year]
    sense = {"max": "<=", "min": ">="}
    ignore = {"max": "unc", "min": 0}

    # Initialize an empty dictionary for global limits
    global_limits = {}

    # Iterate over possible limits and try to read them from the Excel file
    for lim in ["max", "min"]:
        try:
            global_limit = pd.read_excel(
                os.path.join(SCENARIO_SETUP["sub_path"], "extendable_technologies.xlsx"),
                sheet_name=f'{lim}_total_installed',
                index_col=[0, 1, 3, 2, 4],
            ).loc[(SCENARIO_SETUP[f"extendable_{lim}_total"], "global", slice(None), slice(None)), ext_years]
            # If successfully read, add to the global_limits dictionary
            global_limits[lim] = global_limit
        except Exception:
            logging.warning(f"No global {lim} limit found in model file. Skipping.")

    # Now global_limits only contains keys for successfully read sheets
    for lim, global_limit in global_limits.items():
        global_limit.index = global_limit.index.droplevel([0, 1, 2, 3])
        global_limit = global_limit.loc[~(global_limit == ignore[lim]).all(axis=1)]
        constraints = [
            {
                "name": f"global_{lim}-{carrier}-{y}",
                "carrier_attribute": carrier,
                "sense": sense[lim],
                "type": "tech_capacity_expansion_limit",
                **({"investment_period": y} if n.multi_invest else {}),
                "constant": global_limit.loc[carrier, y],
            }
            for carrier in global_limit.index
            for y in ext_years
            if global_limit.loc[carrier, y] != ignore[lim]
        ]

        for constraint in constraints:
            n.add("GlobalConstraint", **constraint)




def set_extendable_limits_per_bus(n):
    ext_years = n.investment_periods if n.multi_invest else [n.snapshots[0].year]
    ignore = {"max": "unc", "min": 0}

    try:
        bus_limits = {
            lim: pd.read_excel(
                os.path.join(SCENARIO_SETUP["sub_path"], "extendable_technologies.xlsx"),
                sheet_name=f'{lim}_total_installed',
                index_col=[0, 1, 3, 2, 4],
            ).loc[(SCENARIO_SETUP[f"extendable_{lim}_total"], SCENARIO_SETUP["regions"], slice(None)), ext_years]
            for lim in ["max", "min"]
        }
    except:
        logging.warning("No regional extendable limits found in model file. Skipping.")
        return

    ext_carriers = (
        list(n.generators.carrier[n.generators.p_nom_extendable].unique())
        + list(n.storage_units.carrier[n.storage_units.p_nom_extendable].unique())
    )
    for lim, bus_limit in bus_limits.items():
        bus_limit.index = bus_limit.index.droplevel([0, 1, 2])
        bus_limit = bus_limit.loc[~(bus_limit == ignore[lim]).all(axis=1)]
        bus_limit = bus_limit.loc[bus_limit.index.get_level_values(1).isin(ext_carriers)]

        for idx in bus_limit.index:
            for y in ext_years:
                if bus_limit.loc[idx, y] != ignore[lim]:
                    n.buses.loc[idx[0],f"nom_{lim}_{idx[1]}_{y}"] = bus_limit.loc[idx, y]


"""
********************************************************************************
    Emissions limits and pricing
********************************************************************************
"""

def calc_emissions(n):
    gen_emissions = pd.read_csv(snakemake.input["generator_emissions"],index_col=[0]) # specified in kgCO2/MWh
    energy = n.generators_t.p[gen_emissions.index].groupby(level=0).sum()
    emissions = pd.Series(0, index = n.investment_periods)

    for y in n.investment_periods:
        emissions[y] = (energy.loc[y] * gen_emissions[str(y)]).sum()

    return emissions/1e9 # Convert from kgCO2/y back to MtCO2/y


def calc_cumulative_new_capacity(n):
    mapping = {
        'Generator':{
            'solar_pv':["solar_pv","solar_pv_low",'solar_pv_rooftop'],
            'wind':['wind','wind_low'],
            'ocgt':['ocgt_diesel','ocgt_avf','ocgt_diesel_emg','ocgt_gas','ocgt_gas_h2_40','ocgt_gas_h2_45','ocgt_gas_h2_50','sasol_gas'],
            'ccgt_steam':['ccgt_steam'],
        },
        'StorageUnit':{
            'battery':["battery_1h","battery_4h",'battery_8h'],     
        }
    }
    
    new_capacity = pd.DataFrame(0, index=n.investment_periods,columns=list(mapping["Generator"].keys()) + list(mapping["StorageUnit"].keys()))
    exist_capacity = pd.Series(0, index=list(mapping["Generator"].keys()) + list(mapping["StorageUnit"].keys()))

    # TODO temp fix for OCGT build year in 2025 being incorrect
    ocgt_list = ["ocgt_diesel","ocgt_avf","ocgt_diesel_emg","ocgt_gas","ocgt_gas_h2_40","ocgt_gas_h2_45","ocgt_gas_h2_50","sasol_gas"]
    gen_list = n.generators.query("carrier in @ocgt_list & p_nom_extendable==False & build_year<=2025").index
    n.generators.loc[gen_list,"build_year"] = 2000


    for c in ["Generator","StorageUnit"]:
        for carrier in mapping[c].keys():
            y=2023
            tech_list = mapping[c][carrier]
            exist_capacity.loc[carrier] += n.df(c).query("carrier==@tech_list & build_year <2024").p_nom_opt.sum()
            
            for y in [2024] + list(n.investment_periods):
                new_capacity.loc[y,carrier] = n.df(c).query("carrier==@tech_list & build_year==@y").p_nom_opt.sum()
    return new_capacity,exist_capacity


def get_capacity_value(n):
    reserve_dual = pd.Series(0, index = n.investment_periods)
    for y in n.investment_periods:
        try:
            reserve_dual.loc[y] = n.model.dual[f"reserve_margin_{y}"].values
        except:
            pass
    return reserve_dual


def add_coal_decom(n, start_limits, full_outages_pu_max):
    """
    Coal is decommissioned by reducing the maximum allowable status of a generator over time. 
    p(h) <= p_nom * p_max_pu(h) * status(h) 
    
    """
    #sign = "<=" if start_limits > 0 else "=="
    endogenous_decom_start_year = snakemake.config["electricity"]["conventional_generators"]["endogenous_decomssioning_start_year"]
    phased_decom = SCENARIO_SETUP["phased_decom"]
    decom_periods = n.investment_periods[n.investment_periods >= endogenous_decom_start_year]
    pre_decom_periods = n.investment_periods[n.investment_periods < endogenous_decom_start_year]

    if phased_decom not in ["None","none","","-"]:
        min_phased_decom = pd.read_excel(
            os.path.join(SCENARIO_SETUP["sub_path"], "phased_decommissioning.xlsx"),
            sheet_name="schedule",
            index_col=[0, 1],
        ).loc[phased_decom].round(2)
    else:
        min_phased_decom = None

    gens_df = n.generators.query("carrier == 'coal' and not p_nom_extendable").index
    gens_df.name = "Generator-com"

    p_nom = n.generators.loc[gens_df, "p_nom"]
    fom = n.generators.loc[gens_df, "capital_cost"]  # only FOM for existing coal fleet

    # Create a retirement variable index
    gen_retire_list=[]
    for y in n.investment_periods:
        active = n.get_active_assets("Generator", y)
        gens_i = [g for g in gens_df if g in active[active]]
        for g in gens_i:
            gen_retire_list.append(f"{g}_{y}")

    gen_retire_list = pd.Index(gen_retire_list, name="Generator-ret")
    n.model.add_variables(lower=0, upper=1, coords=[gen_retire_list], name="Generator-p_nom_ret")

    # Get references to status and retirement variables
    status = n.model.variables["Generator-status"]
    p_nom_ret = n.model.variables["Generator-p_nom_ret"]

    # Empty list to collect terms for the objective function
    retirement_objective_terms = []
    retirement_objective_constant = 0


    for y in n.investment_periods:
        active = n.get_active_assets("Generator", y) # Only take assets that have not yet reached end of life

        gens_i = [g for g in gens_df if g in active[active]] # only apply constraints if station is active
        for gen in gens_i:
            
            gen_list = [f"{gen}_{y}" for y in decom_periods if f"{gen}_{y}" in gen_retire_list]
            retired_y = [f"{gen}_{y_it}" for y_it in decom_periods if y_it <= y]
            retired_y1 = [f"{gen}_{y_it}" for y_it in decom_periods if y_it < y]

            has_ret_vars = len(retired_y) > 0
            
            decom_status = ">=" if SCENARIO_SETUP["endogenous_coal_decom"] and y>=endogenous_decom_start_year else "=="
            p_nom_ret_sum = p_nom_ret.loc[retired_y].sum() 
            p_nom_ret_sum1 = p_nom_ret.loc[retired_y1].sum() 

            if min_phased_decom is not None and has_ret_vars:
                n.model.add_constraints(
                    p_nom_ret_sum,
                    decom_status,
                    min_phased_decom.loc[y, gen],
                    name=f"p_nom_ret_min-{gen}-{y}"
                )
            
            # Operational status changes within year based on start_ups allowed, but limit overall status based on retirements
            op_sign = "<=" if start_limits > 0 else "=="
            n.model.add_constraints(
                status.sel({"period": y, "Generator-com": gen})[1:] # allow slack on first and last timesteps to avoid infeasibilities
                + p_nom_ret_sum,
                op_sign,
                1,
                name=f"status_max-{gen}-{y}",
            )                 
            
            n.model.add_constraints(
                status.sel({"period": y, "Generator-com": gen})[0:1]
                + (p_nom_ret_sum+p_nom_ret_sum1)/2,
                op_sign,
                1,
                name=f"status_max_h0-{gen}-{y}",
            )                      
        
            # Contribution to objective 
            weight = n.investment_period_weightings.loc[y, "objective"]
            retirement_objective_terms.append(
                -fom.loc[gen] * weight * p_nom.loc[gen] * p_nom_ret_sum
            )  
            retirement_objective_constant += -fom.loc[gen] * weight * p_nom.loc[gen] * -min_phased_decom.loc[y, gen]  # subtract FOM from saving for what would have been partially decommissioned anyway


            # Limit number of intra-year starts based on SL_X specification, but adjust to remove decomissioned capacity
            if start_limits > 0:
                start_ups = n.model.variables['Generator-start_up'].sel({"Generator-com":gen}).loc[n.snapshots[n.snapshots.get_level_values(0)==y][1:]].sum() # ignore first snapshot of year
                status_start = n.model.variables['Generator-status'].sel({"Generator-com":gen, "period": y, "timestep": n.snapshots.get_level_values(1)[n.snapshots.get_level_values(0)==y][0]})
                status_end = n.model.variables['Generator-status'].sel({"Generator-com":gen, "period": y, "timestep":n.snapshots.get_level_values(1)[n.snapshots.get_level_values(0)==y][-1]})

                n.model.add_constraints(
                    start_ups + start_limits * p_nom_ret_sum <= start_limits + 0.001, # add a small epsilon to avoid numerical issues
                    name = f'coal_startup_limits-{gen}-{y}',
                )

                n.model.add_constraints(
                    status_start <= status_end, # add a small epsilon to avoid numerical issues
                    name = f'coal_startup_shutdown_balance-{gen}-{y}',
                )

    n.model.objective += sum(retirement_objective_terms)

    if retirement_objective_constant > 0:
        object_const = n.model.add_variables(retirement_objective_constant, retirement_objective_constant, name="retirement_objective_constant")
        n.model.objective += -1 * object_const

    n.retirement_objective_constant = retirement_objective_constant
    n.objective_constant += retirement_objective_constant


def remove_min_up_down_time_constraints(n):
    """
    Remove the minimum up and down time constraints for committable generators.
    """

    logging.warning(f"Removing minimum up and down time constraints for committable generators. Only applicable if two-shifting is not used.")
    
    n.model.remove_constraints(
        ["Generator-com-up-time", "Generator-com-down-time", "Generator-com-status-min_up_time_must_stay_up"]
    )


def set_operating_reserves(n, sns, SCENARIO_SETUP):
   
    reserves = pd.read_excel(
        os.path.join(SCENARIO_SETUP["sub_path"],"operational_constraints.xlsx"), 
        sheet_name = "operational_reserves",
        index_col = [0,1],
    ).loc[SCENARIO_SETUP["operational_reserves"]].T

    #### Dispatchable generators - coal always on can also add reserves
    reserve_carriers = snakemake.config["electricity"]["operating_reserve_carriers"]
    gens_i = n.generators.query("carrier in @reserve_carriers").index    
    fix_i = n.generators.query("carrier in @reserve_carriers and not p_nom_extendable and not committable").index
    ext_i = n.generators.query("carrier in @reserve_carriers and p_nom_extendable").index
    com_i = n.generators.query("carrier in @reserve_carriers and committable").index
    
    status = n.model.variables["Generator-status"].rename({"Generator-com":"Generator"})

    active = get_activity_mask(n, "Generator", sns, gens_i)
    active.index.name="snapshot"
    
    p_nom_fix = DataArray(n.generators.loc[fix_i, "p_nom"] * active[fix_i])
    p_nom_com = status * DataArray(n.generators.loc[com_i, "p_nom"]) * active[com_i]
        
    p_nom_ext = n.model.variables["Generator-p_nom"].sel({"Generator-ext":ext_i}).rename({"Generator-ext":"Generator"}) * DataArray(active[ext_i])
    
    p_fix = n.model.variables["Generator-p"].sel(Generator=fix_i, snapshot=sns)
    p_ext = n.model.variables["Generator-p"].sel(Generator=ext_i, snapshot=sns)
    p_com = n.model.variables["Generator-p"].sel(Generator=com_i, snapshot=sns)
    p = n.model.variables["Generator-p"].sel(Generator=gens_i, snapshot=sns)

    p_max_pu = DataArray(n.get_switchable_as_dense("Generator", "p_max_pu").loc[sns])
    n.model.add_variables(lower=0, coords=n.model.variables["Generator-p"].sel(Generator=gens_i, snapshot=sns).coords, name="Generator-op_res", mask=active)  
        
    # Add dispatchable generator total reserves
    op_res_lhs = n.model.variables["Generator-op_res"].sel(Generator=fix_i,snapshot=sns) + p_fix
    op_res_rhs = p_max_pu.sel(Generator=fix_i, snapshot=sns)  * p_nom_fix
    n.model.add_constraints(op_res_lhs <= op_res_rhs, name="Generator-fix-op_res", mask=active[fix_i])

    op_res_lhs = n.model.variables["Generator-op_res"].sel(Generator=ext_i,snapshot=sns) + p_ext - p_max_pu.sel(Generator=ext_i, snapshot=sns)  * p_nom_ext 
    n.model.add_constraints(op_res_lhs <= 0, name="Generator-ext-op_res", mask=active[ext_i])
    op_res_rhs = p_max_pu.sel(Generator=fix_i, snapshot=sns)  * p_nom_fix

    op_res_lhs = n.model.variables["Generator-op_res"].sel(Generator=com_i,snapshot=sns) + p_com - p_max_pu.sel(Generator=com_i, snapshot=sns)  * p_nom_com 
    n.model.add_constraints(op_res_lhs <= 0, name="Generator-com-op_res", mask=active[com_i])

    #### Energy storage
    fix_i = n.storage_units.query("p_nom_extendable == False").index
    ext_i = n.storage_units.query("p_nom_extendable == True").index
    st_i = n.storage_units.index
    active = get_activity_mask(n, "StorageUnit", sns, st_i)
    active.index.name="snapshot"

    p_nom_st_fix = DataArray(n.storage_units.loc[fix_i, "p_nom"]) * DataArray(active[fix_i])
    p_nom_st_ext = n.model.variables["StorageUnit-p_nom"].sel({"StorageUnit-ext":ext_i}).rename({"StorageUnit-ext":"StorageUnit"}) * DataArray(active[ext_i])

    st_p_max_pu = n.get_switchable_as_dense("StorageUnit", "p_max_pu")

    n.model.add_variables(lower=0, coords=n.model.variables["StorageUnit-p_store"].sel(snapshot=sns).coords, name="StorageUnit-op_res", mask = active)
    p_store = n.model.variables["StorageUnit-p_store"].sel(snapshot=sns)
    p_dispatch = n.model.variables["StorageUnit-p_dispatch"].sel(snapshot=sns)

    st_res_lhs1 = n.model.variables["StorageUnit-op_res"].sel(StorageUnit = fix_i, snapshot=sns) + p_dispatch.sel(StorageUnit = fix_i, snapshot=sns)  - p_store.sel(StorageUnit = fix_i, snapshot=sns) #- st_p_max_pu_ext * p_nom_st_ext
    st_res_rhs1 = DataArray(st_p_max_pu[fix_i]) * p_nom_st_fix
    n.model.add_constraints(st_res_lhs1 <= st_res_rhs1, name="StorageUnit-fix-res1", mask=active[fix_i])

    st_res_lhs2 = n.model.variables["StorageUnit-op_res"].sel(StorageUnit=fix_i, snapshot=sns)
    st_res_rhs2 = n.model.variables["StorageUnit-state_of_charge"].sel(StorageUnit=fix_i, snapshot=sns) + p_store.sel(StorageUnit = fix_i, snapshot=sns) 
    n.model.add_constraints(st_res_lhs2 <= st_res_rhs2, name="StorageUnit-fix-res2", mask =active[fix_i])

    st_res_lhs1 = n.model.variables["StorageUnit-op_res"].sel(StorageUnit=ext_i, snapshot=sns) + p_dispatch.sel(StorageUnit = ext_i, snapshot=sns) - p_store.sel(StorageUnit = ext_i, snapshot=sns) - st_p_max_pu[ext_i] * p_nom_st_ext
    n.model.add_constraints(st_res_lhs1 <= 0, name="StorageUnit-ext-res1")

    st_res_lhs2 = n.model.variables["StorageUnit-op_res"].sel(StorageUnit=ext_i,snapshot=sns)
    st_res_rhs2 = n.model.variables["StorageUnit-state_of_charge"].sel(StorageUnit=ext_i,snapshot=sns) + p_store.sel(StorageUnit = ext_i, snapshot=sns)
    n.model.add_constraints(st_res_lhs2 <= st_res_rhs2, name="StorageUnit-ext-res2", mask= active[ext_i])

    #### Total reserves

    tot_res = (
        n.model.variables["Generator-op_res"].sum("Generator") 
        + n.model.variables["StorageUnit-op_res"].sum("StorageUnit")
    )

    reserve_requirements = pd.DataFrame(index=sns, columns= ["total"])
    for y in sns.get_level_values(0).unique():
        reserve_requirements.loc[y, "total"] = reserves.loc[y, "total"]
    reserve_requirements.columns.name = "_type"
    reserve_requirements = DataArray(reserve_requirements)

    n.model.add_constraints(tot_res >= reserve_requirements.sel(_type="total"), name="Operating_reserves")

    return

def solve_network(n, sns, full_outages_pu_max):

    n.optimize.create_model(snapshots = sns, multi_investment_periods = n.multi_invest, linearized_unit_commitment = True)    

    set_operational_limits(n, sns, SCENARIO_SETUP, snakemake)
    ccgt_steam_constraints(n, sns, SCENARIO_SETUP, snakemake)
    #set_operating_reserves(n, sns, SCENARIO_SETUP)
        
    if SCENARIO_SETUP["unit_committment"]:
        start_limits = float(SCENARIO_SETUP["dispatch_coal_flex"].split("_")[1])
        add_coal_decom(n, start_limits, full_outages_pu_max)
        param = load_extendable_parameters(n, SCENARIO_SETUP, snakemake)
        if SCENARIO_SETUP["carbon_constraints"] not in ["None", "none", "", "-"]:
            gen_emissions = pd.read_csv(snakemake.input["generator_emissioans"],index_col=[0])
            add_annual_co2_constraints(n, sns, param, SCENARIO_SETUP, gen_emissions)


    reserve_margin_constraints(n, sns, SCENARIO_SETUP, snakemake)

    if SCENARIO_SETUP["unit_committment"]:
        remove_min_up_down_time_constraints(n)
    n.optimize.solve_model(solver_name=SOLVER_NAME, solver_options=SOLVER_OPTIONS)


def scale_costs(n, scaling_factor):

    for c in ["Generator", "StorageUnit", "Link"]:

        static_cost_param = [col for col in n.df(c).columns if "cost" in col]
        for p in static_cost_param:
            n.df(c)[p] = n.df(c)[p] / scaling_factor

        dynamic_cost_param = [col for col in n.pnl(c).keys() if "cost" in col]
        for p in dynamic_cost_param:
            n.pnl(c)[p] = n.pnl(c)[p] / scaling_factor

def add_noisy_costs(n, config):
    for c in ["Generator", "StorageUnit", "Link"]:
        mc = n.pnl(c)["marginal_cost"] 
        random_adj = 2e-3*(np.random.random(len(mc.columns)) - 0.5) * 10
        for col in range(len(mc.columns)):
            n.pnl(c)["marginal_cost"].iloc[:,col] += random_adj[col] 



if __name__ == "__main__":
    if 'snakemake' not in globals():
        from _helpers import mock_snakemake
        snakemake = mock_snakemake(
            'prepare_and_solve_network', 
            **{
                'scenario':"S1",
            }
        )
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)s: %(asctime)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    logging.info("Loading network")

    # Set fixed seed for random to ensure reproducibility
    RANDOM_SEED = snakemake.config["random_seed"]
    np.random.seed(RANDOM_SEED)

    n = pypsa.Network(snakemake.input[0])

    n.generators.ramp_limit_start_up = 0.65

    n.generators.ramp_limit_shut_down = 0.65   

    SCENARIO_SETUP = load_scenario_definition(snakemake.wildcards.scenario, snakemake.config)

    SOLVER_NAME = snakemake.config["solver"][SCENARIO_SETUP["solver"]].pop("name")
    SOLVER_OPTIONS = snakemake.config["solver"][SCENARIO_SETUP["solver"]].copy()


    logging.info("Setting global and regional build limits")
    if len(n.buses) != 1: #covered under single bus limits
        set_extendable_limits_global(n) 
    set_extendable_limits_per_bus(n)

    logging.info("Solving network")

    full_outages_pu_max = pd.DataFrame()

    n = check_pu_profiles(n, snakemake.config["electricity"]["clean_pu_profiles"])

    if snakemake.config["costs"]["noisy_costs"]:
        add_noisy_costs(n, snakemake.config)

    scale_costs(n, 1e3)
    solve_network(n, n.snapshots, full_outages_pu_max)

    n.export_to_netcdf(snakemake.output[0])
    n.statistics().to_csv(snakemake.output[1])
    n.generators.to_csv(snakemake.output[2])
    n.storage_units.to_csv(snakemake.output[3])
    get_capacity_value(n).to_csv(snakemake.output[4])
    
    try:
        n.model.solution["Generator-p_nom_ret"].to_dataframe().to_csv(snakemake.output[5])
    except:
        # create empty dataframe if no retirement variables are present to avoid breaking snakemake workflow
        pd.DataFrame().to_csv(snakemake.output[5])
    full_outages_pu_max.to_csv(snakemake.output[6])
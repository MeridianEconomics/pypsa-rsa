import logging
import pandas as pd
import pypsa
from pypsa.descriptors import get_switchable_as_dense as get_as_dense, expand_series, get_activity_mask
from pypsa.optimization.common import reindex
import os

from _helpers import get_investment_periods
# from add_electricity import load_costs, update_transmission_costs

import xarray as xr
import warnings
warnings.simplefilter(action='ignore', category=FutureWarning) # Comment out for debugging and development

idx = pd.IndexSlice
logger = logging.getLogger(__name__)


"""
********************************************************************************
    Operational limits
********************************************************************************
"""

def calc_max_gen_potential(n, sns, gens, incl_pu, weightings, active, cf_limit, extendable=False):
    suffix = "" if extendable == False else "-ext"
    p_max_pu = get_as_dense(n, 'Generator', "p_max_pu", sns)[gens] if incl_pu else pd.DataFrame(1, index=sns, columns=gens)
    p_max_pu.columns.name = f'Generator{suffix}'
    
    if n.multi_invest:
        cf_limit_h = pd.DataFrame(0, index=sns, columns=gens)
        for y in cf_limit_h.index.get_level_values(0).unique():
            cf_limit_h.loc[y] = cf_limit[y]
    else:
        cf_limit_h = pd.DataFrame(cf_limit[sns[0].year], index=sns, columns=gens) * weightings[gens]
    
    if not extendable:
        return (cf_limit_h[gens] * active[gens] * p_max_pu * weightings[gens] * n.generators.loc[gens, "p_nom"]).sum(axis=1)
    p_nom = n.model.variables["Generator-p_nom"].sel({f"Generator{suffix}": gens})
    potential = xr.DataArray(cf_limit_h[gens] * active[gens] * p_max_pu * weightings[gens])
    potential = potential.rename({"Generator":"Generator-ext"}) if "Generator" in potential.dims else potential
    
    return (potential * p_nom).sum(f'Generator{suffix}')

def group_and_sum(data, groupby_func):
    grouped_data = data.groupby(groupby_func).sum()
    return grouped_data.sum(axis=1) if len(grouped_data) > 1 else grouped_data
    
def apply_operational_constraints(n, sns, **kwargs):
    energy_unit_conversion = {"GW":1e3, "GJ": 1/3.6, "TJ": 1000/3.6, "PJ": 1e6/3.6, "GWh": 1e3, "TWh": 1e6}
    apply_to = kwargs["apply_to"]

    carrier = [c.strip() for c in kwargs["carrier"].split("+")]
    bus = kwargs["bus"]
    period = kwargs["period"]
    type_ = "energy_power" if kwargs["type"] in ["primary_energy", "output_energy", "output_power"] else "capacity_factor"

    if (period  == "week") & (max(n.snapshot_weightings["generators"])>1):
        logger.warning(
            "Applying weekly operational limits and time segmentation should be used with caution as the snapshot weightings might not align with the weekly grouping."
        )
    incl_pu = kwargs["incl_pu"]
    limit = kwargs["limit"]

    sense = "<=" if limit == "max" else ">="

    if len(sns) <8760 and not n.multi_invest and period in ["week", "month"]: #conditions for rolling horizon analysis monthly
        sns = sns[sns.month == sns.month[0]] # ignore overlap with next month for monthly limits

    cf_limit = 0 * kwargs["values"] if type_ == "energy_power" else kwargs["values"]
    en_pow_limit = 0 * kwargs["values"] if type_ == "capacity_factor" else kwargs["values"]

    if ((kwargs["type"] in ["primary_energy", "output_energy"]) & (kwargs["units"] != "MWh")) or ((kwargs["type"] == "output_power") & (kwargs["units"] != "MW")):
        en_pow_limit *= energy_unit_conversion[kwargs["units"]]

    years = get_investment_periods(sns, n.multi_invest)

    filtered_gens = n.generators.query("carrier in @carrier") if len(carrier)>1 else n.generators.query("carrier == @carrier")
    if bus != "global":
        filtered_gens = filtered_gens.query("bus == @bus")
    fix_i = filtered_gens.query("not p_nom_extendable").index if apply_to in ["fixed", "all"] else []
    ext_i = filtered_gens.query("p_nom_extendable").index if apply_to in ["extendable", "all"] else []
    filtered_gens = filtered_gens.loc[list(fix_i) + list(ext_i)]

    if len(filtered_gens) == 0:
        return

    efficiency = get_as_dense(n, "Generator", "efficiency", inds=filtered_gens.index) if kwargs["type"] == "primary_energy" else pd.DataFrame(1, index=n.snapshots, columns = filtered_gens.index)
    weightings = (1/efficiency).multiply(n.snapshot_weightings.generators, axis=0)

    # if only extendable generators only select snapshots where generators are active
    min_year = n.generators.loc[filtered_gens.index, "build_year"].min()
    sns_active = sns[sns.get_level_values(0) >= min_year] if n.multi_invest else sns[sns.year >= min_year]
    act_gen = (n.model.variables['Generator-p'].loc[sns_active, filtered_gens.index] * weightings.loc[sns_active]).sel(Generator=filtered_gens.index).sum('Generator')
    act_gen_pow = (n.model.variables['Generator-p'].loc[sns_active, filtered_gens.index]).sel(Generator=filtered_gens.index).sum('Generator')

    timestep = "timestep" if n.multi_invest else "snapshot"
    groupby_dict = {
        "year": f"{timestep}.year",
        "month": f"{timestep}.month",
        "week": f"{timestep}.week",
        "hour": None
    }

    active = get_activity_mask(n, "Generator", sns).astype(int)
    if type_ != "energy_power":
        max_gen_fix = calc_max_gen_potential(n, sns, fix_i, incl_pu, weightings, active, cf_limit, extendable=False) if len(fix_i)>0 else 0
        max_gen_ext = calc_max_gen_potential(n, sns, ext_i, incl_pu, weightings, active, cf_limit, extendable=True) if len(ext_i)>0 else 0

    if groupby := groupby_dict[period]:
        for y in years:
            year_sns = sns_active[sns_active.get_level_values(0)==y] if n.multi_invest else sns_active
            if len(year_sns) > 0:
                if type_ == "capacity_factor":
                    lhs = (act_gen - max_gen_ext) 
                    if isinstance(max_gen_fix, (int, float)):
                        rhs = max_gen_fix
                        skip_constraint = 0 if rhs >=0 else 1
                    else:
                        rhs = max_gen_fix.loc[y] if n.multi_invest else max_gen_fix.loc[year_sns]
                        skip_constraint = 0 if (rhs >=0).any().any() else 1
                else:
                    lhs = act_gen
                    rhs = en_pow_limit[y]
                    skip_constraint = 0 if rhs >0 else 1 
                if not skip_constraint:
                    lhs = lhs.sel(snapshot=year_sns)
                    lhs_p = lhs.sum() if period == "year" else lhs.groupby(groupby).sum()
                    rhs_p = (
                        rhs
                        if isinstance(rhs, (int, float))
                        else xr.DataArray(rhs).groupby(groupby).sum()
                    )
                    n.model.add_constraints(lhs_p, sense, rhs_p, name=f'{limit}-{kwargs["carrier"]}-{period}-{kwargs["apply_to"][:3]}-{y}')

    else:

        lhs = (act_gen - max_gen_ext).sel(snapshot = sns_active) if type_ == "capacity_factor" else act_gen_pow.sel(snapshot = sns_active)
        if kwargs["type"] == "output_energy":
            logging.warning("Energy limits are not yet implemented for hourly operational limits.")
            return

        if type_ == "capacity_factor":
            if isinstance(max_gen_fix, int):
                rhs = max_gen_fix
            else:
                rhs = xr.DataArray(max_gen_fix.loc[sns_active])
                rhs = rhs.rename({"dim_0": "snapshot"}) if rhs.dims[0] == "dim_0" else rhs # in the case of index name not being snapshot

        else:
            rhs = pd.Series(index = sns)
            if n.multi_invest:
                for y in years:
                    rhs.loc[y] = en_pow_limit[y]
            else:
                rhs.loc[str(years[0])] = en_pow_limit[years[0]]

        n.model.add_constraints(lhs, sense, rhs, name = f'{limit}-{kwargs["carrier"]}-hour-{kwargs["apply_to"][:3]}')

def set_operational_limits(n, sns, scenario_setup, snakemake, exclude_flag=[]):

    op_limits = pd.read_excel(
        os.path.join(scenario_setup["sub_path"], "operational_constraints.xlsx"),
        sheet_name='operational_constraints',
        index_col=list(range(9)),
    )

    if scenario_setup["operational_limits"] not in op_limits.index.get_level_values(0).unique():
        logging.warning(f"Operational limits for scenario {scenario_setup['operational_limits']} not found, skipping.")
        return
    op_limits = op_limits.loc[scenario_setup["operational_limits"]]

    if len(exclude_flag) > 0:
        logging.warning(f"Excluding operational limits that are specified in {exclude_flag}.")
        op_limits = op_limits.loc[~op_limits.index.get_level_values(3).str.contains("|".join(exclude_flag))]

    #drop rows where all NaN
    op_limits = op_limits.loc[~(op_limits.isna().all(axis=1))]
    for idx, row in op_limits.iterrows():
        apply_operational_constraints(
            n, sns, 
            bus = idx[0], carrier = idx[1], 
            type = idx[2], values = row, 
            period = idx[3], incl_pu = idx[4],
            limit = idx[5], apply_to = idx[6],
            units = idx[7],
        )

def ccgt_steam_constraints(n, sns, scenario_setup, snakemake):
    # At each bus HRSG power is limited by what OCGT power production at that bus
    config = snakemake.config["electricity"]["conventional_generators"]
    p_nom_ratio = config["ccgt_st_to_gt_ratio"]

    ocgt_carriers = pd.read_excel(
            os.path.join(scenario_setup["sub_path"], "aux_stg_feed.xlsx"), 
            sheet_name='allowable_carriers',
            index_col=[0,1,2],
    ).loc[scenario_setup["aux_stg_feed"]]

    years = n.investment_periods if n.multi_invest else [n.snapshots[0].year]
    
    for bus in n.buses.index:
        for y in years:

            carriers = ocgt_carriers[y]
            carriers =carriers[carriers==True]
            carriers = carriers.reset_index().set_index("carrier")

            fix_carriers = carriers[(carriers["apply_to"] == "fixed") | (carriers["apply_to"] == "all")].index
            ext_carriers = carriers[(carriers["apply_to"] == "extendable") | (carriers["apply_to"] == "all")].index
            
            fix_ocgt_gens = n.generators.query("bus == bus & carrier in @fix_carriers & p_nom_extendable == 0").index
            ext_ocgt_gens = n.generators.query("bus == bus & carrier in @ext_carriers & p_nom_extendable == 1").index
            ocgt_gens = list(fix_ocgt_gens) + list(ext_ocgt_gens)

            if len(n.investment_periods) > 0:
                sns_y = sns[sns.get_level_values(0)==y]
                ccgt_hrsg = n.generators[n.get_active_assets("Generator",y)].query("bus == bus & carrier == 'ccgt_steam'").index
            else:
                sns_y = sns
                ccgt_hrsg = n.generators.query("bus == bus & carrier == 'ccgt_steam'").index

            if len(ccgt_hrsg) == 0:
                continue
            lhs = (n.model.variables['Generator-p'].loc[sns_y, ccgt_hrsg] - p_nom_ratio*n.model.variables['Generator-p'].loc[sns_y, ocgt_gens]).sum("Generator")
            rhs = 0
            n.model.add_constraints(lhs, "<=", rhs, name = f'ccgt_steam_limit-{bus}-{y}')

"""
********************************************************************************
    Limit number of start-ups for coal plant
********************************************************************************
"""
def limit_coal_start_ups_capacity(n, sns, limit, full_outages_pu_max):

    delta_full_outages_up = full_outages_pu_max.diff().fillna(0)
    delta_full_outages_up[delta_full_outages_up<0] = 0
    delta_full_outages_up = delta_full_outages_up.groupby(level=0).sum()
    delta_full_outages_up.columns.name = "Generator-com" # reindex for Xarray

    # Limit coal plant start-ups
    coal_gens = n.generators.query("carrier == 'coal' & committable").index
    lhs = n.model.variables['Generator-start_up'].sel({"Generator-com":coal_gens}).groupby("period").sum()
    rhs = limit + delta_full_outages_up
    n.model.add_constraints(lhs, "<=", rhs, name = f'coal_startup_limits')

"""
********************************************************************************
    Reserve margin
********************************************************************************
"""
def check_active(n, c, y, list):
    active = n.df(c).index[n.get_active_assets(c, y)] if n.multi_invest else list
    return list.intersection(active)

def reserve_margin_constraints(n, sns, scenario_setup, snakemake):
    ###################################################################################
    # Reserve margin above maximum peak demand in each year
    # The sum of res_margin_carriers multiplied by their assumed constribution factors 
    # must be higher than the maximum peak demand in each year by the reserve_margin value

    endogenous_decom_start_year = snakemake.config["electricity"]["conventional_generators"]["endogenous_decomssioning_start_year"]
    decom_periods = n.investment_periods[n.investment_periods >= endogenous_decom_start_year]


    res_margin = pd.read_excel(
        os.path.join(scenario_setup["sub_path"], "reserve_margin.xlsx"), 
        sheet_name="reserve_margin",
        index_col=[0,1]).loc[scenario_setup["reserve_margin"]].drop("units", axis=1)

    capacity_credit = pd.read_excel(
            os.path.join(scenario_setup["sub_path"], "reserve_margin.xlsx"), 
            sheet_name="capacity_credits",
            index_col=[0])[scenario_setup["capacity_credits"]]

    res_mrgn_active = res_margin.loc["reserve_margin_active"]
    res_mrgn = res_margin.loc["reserve_margin"]

    peak = n.loads_t.p_set.loc[sns].sum(axis=1).groupby(sns.get_level_values(0)).max() if n.multi_invest else n.loads_t.p_set.loc[sns].sum(axis=1).max()
    peak = peak if n.multi_invest else pd.Series(peak, index = sns.year.unique())
    #capacity_credit = snakemake.config["electricity"]["reserves"]["capacity_credit"]

    for y in peak.index:
        if res_mrgn_active[y]:    

            fix_i = n.generators.query("not p_nom_extendable & carrier in @capacity_credit").index
            ext_i = n.generators.query("p_nom_extendable & carrier in @capacity_credit").index
    
            fix_cap = 0
            lhs = 0
            for c in ["Generator", "StorageUnit"]:
                fix_i = n.df(c).query("not p_nom_extendable & carrier in @capacity_credit.index").index
                fix_i = check_active(n, c, y, fix_i)

                fix_cap += (
                    n.df(c).loc[fix_i, "carrier"].map(capacity_credit)
                    * n.df(c).loc[fix_i, "p_nom"]
                ).sum()
            
                ext_i = n.df(c).query("p_nom_extendable & carrier in @capacity_credit.index").index
                ext_i = check_active(n, c, y, ext_i)
    
                lhs += (
                    n.model.variables[f"{c}-p_nom"].sel({f"{c}-ext":ext_i}) 
                    *xr.DataArray(n.df(c).loc[ext_i, "carrier"].map(capacity_credit)).rename({f"{c}":f"{c}-ext"})
                ).sum(f"{c}-ext")

            #### Remove decommisioned coal from the reserve margin
            coal_decom = 0
            if scenario_setup["endogenous_coal_decom"]:
                coal_gens = n.generators.query("carrier == 'coal' and not p_nom_extendable").index
                coal_gens = check_active(n, "Generator", y, coal_gens)
                for gen in coal_gens:
                    p_nom = n.generators.loc[gen, "p_nom"]
                    retired_sum= [f"{gen}_{y_it}" for y_it in decom_periods if y_it <= y]    
                    p_nom_retired = n.model.variables["Generator-p_nom_ret"].sel({"Generator-ret":retired_sum}).sum()
                    if len(retired_sum) > 0:
                        coal_decom += capacity_credit.loc["coal"] * p_nom * p_nom_retired

            if coal_decom !=0:
                lhs = lhs - coal_decom
            rhs = peak.loc[y]*(1+res_mrgn[y]) - fix_cap
            n.model.add_constraints(lhs, ">=", rhs, name = f"reserve_margin_{y}")    

def add_annual_co2_constraints(n, sns, scenario_setup, gen_emissions):

    annual_limits = pd.read_excel(
        os.path.join(scenario_setup["sub_path"], "emissions.xlsx"), 
        sheet_name="annual_carbon_constraint",
        index_col=[0]).loc[scenario_setup["carbon_constraints"]]

    conv = 1
    if annual_limits.unit.split("/")[0] == "Mt":
        conv = 1e9 # convert to kgCO2
    elif annual_limits.units.split("/")[0] == "Gt":
        conv = 1e12 # convert to kgCO2

    gen_p = n.model.variables['Generator-p']
    gen_p = gen_p.sel(Generator = gen_emissions.index)

    for y in n.investment_periods:
        lhs = gen_p.sel(period=y).sum("timestep") * gen_emissions[str(y)]
        rhs = (annual_limits.loc[y] * conv)
        n.model.add_constraints(lhs, "<=", rhs, name = f'annual_carbon_limits_{y}')

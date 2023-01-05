# SPDX-FileCopyrightText: : 2017-2022 The PyPSA-EUR Authors, The PyPSA-Earth Authors, The PyPSA-ZA Authors
# SPDX-License-Identifier: MIT

# coding: utf-8

"""
Adds electrical generators, load and existing hydro storage units to a base network.

Relevant Settings
-----------------

.. code:: yaml

    costs:
        year:
        USD_to_ZAR:
        EUR_to_ZAR:
        marginal_cost:
        dicountrate:
        emission_prices:
        load_shedding:

    electricity:
        max_hours:
        marginal_cost:
        capital_cost:
        conventional_carriers:
        co2limit:
        extendable_carriers:
        include_renewable_capacities_from_OPSD:
        estimate_renewable_capacities_from_capacity_stats:

    load:
        scale:
        ssp:
        weather_year:
        prediction_year:
        region_load:

    renewable:
        hydro:
            carriers:
            hydro_max_hours:
            hydro_capital_cost:

    lines:
        length_factor:

.. seealso::
    Documentation of the configuration file ``config.yaml`` at :ref:`costs_cf`,
    :ref:`electricity_cf`, :ref:`load_cf`, :ref:`renewable_cf`, :ref:`lines_cf`

Inputs
------

- ``data/costs.csv``: The database of cost assumptions for all included technologies for specific years from various sources; e.g. discount rate, lifetime, investment (CAPEX), fixed operation and maintenance (FOM), variable operation and maintenance (VOM), fuel costs, efficiency, carbon-dioxide intensity.
- ``data/bundle/hydro_capacities.csv``: Hydropower plant store/discharge power capacities, energy storage capacity, and average hourly inflow by country.  Not currently used!

    .. image:: ../img/hydrocapacities.png
        :scale: 34 %

- ``data/geth2015_hydro_capacities.csv``: alternative to capacities above; not currently used!
- ``resources/ssp2-2.6/2030/era5_2013/Africa.nc`` Hourly country load profiles produced by GEGIS
- ``resources/regions_onshore.geojson``: confer :ref:`busregions`
- ``resources/gadm_shapes.geojson``: confer :ref:`shapes`
- ``resources/powerplants.csv``: confer :ref:`powerplants`
- ``resources/profile_{}.nc``: all technologies in ``config["renewables"].keys()``, confer :ref:`renewableprofiles`.
- ``networks/base.nc``: confer :ref:`base`

Outputs
-------

- ``networks/elec.nc``:

    .. image:: ../img/elec.png
            :scale: 33 %

Description
-----------

The rule :mod:`add_electricity` ties all the different data inputs from the preceding rules together into a detailed PyPSA network that is stored in ``networks/elec.nc``. It includes:

- today's transmission topology and transfer capacities (in future, optionally including lines which are under construction according to the config settings ``lines: under_construction`` and ``links: under_construction``),
- today's thermal and hydro power generation capacities (for the technologies listed in the config setting ``electricity: conventional_carriers``), and
- today's load time-series (upsampled in a top-down approach according to population and gross domestic product)

It further adds extendable ``generators`` with **zero** capacity for

- photovoltaic, onshore and AC- as well as DC-connected offshore wind installations with today's locational, hourly wind and solar capacity factors (but **no** current capacities),
- additional open- and combined-cycle gas turbines (if ``OCGT`` and/or ``CCGT`` is listed in the config setting ``electricity: extendable_carriers``)
"""


from email import generator
import logging
import os
import geopandas as gpd
import numpy as np
import pandas as pd
import powerplantmatching as pm
import pypsa
import xarray as xr
from _helpers import (configure_logging, 
                    getContinent, 
                    update_p_nom_max, 
                    pdbcast, 
                    map_generator_parameters, 
                    clean_pu_profiles)

from shapely.validation import make_valid
from shapely.geometry import Point
from vresutils import transfer as vtransfer
idx = pd.IndexSlice
logger = logging.getLogger(__name__)
from pypsa.descriptors import get_switchable_as_dense as get_as_dense

def normed(s):
    return s / s.sum()

def remove_leap_day(df):
    return df[~((df.index.month == 2) & (df.index.day == 29))]

def calculate_annuity(n, r):
    """
    Calculate the annuity factor for an asset with lifetime n years and
    discount rate of r, e.g. annuity(20, 0.05) * 20 = 1.6
    """
    if isinstance(r, pd.Series):
        return pd.Series(1 / n, index=r.index).where(
            r == 0, r / (1.0 - 1.0 / (1.0 + r) ** n)
        )
    elif r > 0:
        return r / (1.0 - 1.0 / (1.0 + r) ** n)
    else:
        return 1 / n

def _add_missing_carriers_from_costs(n, costs, carriers):
    missing_carriers = pd.Index(carriers).difference(n.carriers.index)
    if missing_carriers.empty: return

    emissions_cols = costs.columns.to_series()\
                           .loc[lambda s: s.str.endswith('_emissions')].values
    suptechs = missing_carriers.str.split('-').str[0]
    emissions = costs.loc[suptechs, emissions_cols].fillna(0.)
    emissions.index = missing_carriers
    n.import_components_from_dataframe(emissions, 'Carrier')

def load_costs(model_file, cost_scenario, config, elec_config, config_years):
    """
    set all asset costs and other parameters
    """
    cost_data = pd.read_excel(model_file, sheet_name='costs',index_col=list(range(3))).sort_index().loc[cost_scenario]
    cost_data.drop('source',axis=1,inplace=True)
    # Interpolate for years in config file but not in cost_data excel file
    config_years_array = np.array(config_years)
    missing_year = config_years_array[~np.isin(config_years_array,cost_data.columns)]
    if len(missing_year) > 0:
        for i in missing_year: 
            cost_data.insert(0,i,np.nan) # add columns of missing year to dataframe
        cost_data_tmp = cost_data.drop('unit',axis=1).sort_index(axis=1)
        cost_data_tmp = cost_data_tmp.interpolate(axis=1)
        cost_data = pd.concat([cost_data_tmp, cost_data['unit']],ignore_index=False,axis=1)

    # correct units to MW and ZAR
    cost_data.loc[cost_data.unit.str.contains("/kW")==True, config_years] *= 1e3
    cost_data.loc[cost_data.unit.str.contains("USD")==True, config_years] *= config["USD_to_ZAR"]
    cost_data.loc[cost_data.unit.str.contains("EUR")==True, config_years] *= config["EUR_to_ZAR"]

    fom_perc_capex=cost_data.loc[cost_data.unit.str.contains("%/year")==True, config_years]
    fom_perc_capex=fom_perc_capex.index.get_level_values(0)

    costs = {}
    for y in config_years:
        costs[y]=cost_data.loc[idx[:, y]].unstack(level=1).fillna(
        {
            "CO2 intensity": 0,
            "FOM": 0,
            "VOM": 0,
            "discount rate": config["discountrate"],
            "efficiency": 1,
            "efficiency_store": 1,
            "efficiency_dispatch": 1,
            "fuel": 0,
            "investment": 0,
            "lifetime": 25,
        })

        costs[y]['efficiency_store']=costs[y]['efficiency'].pow(1./2) #if only 1 efficiency value is given assume it is round trip efficiency
        costs[y]['efficiency_dispatch']=costs[y]['efficiency'].pow(1./2)
        costs[y].loc[fom_perc_capex,'FOM']*=costs[y].loc[fom_perc_capex,"investment"]/100.0
        costs[y]["capital_cost"] = (costs[y]["investment"]*
                                    calculate_annuity(costs[y]["lifetime"], costs[y]["discount rate"])
                                    + costs[y]["FOM"])

        costs[y].at["OCGT", "fuel"] = costs[y].at["gas", "fuel"]
        costs[y].at["CCGT", "fuel"] = costs[y].at["gas", "fuel"]

        costs[y]["marginal_cost"] = costs[y]["VOM"] + costs[y]["fuel"] / costs[y]["efficiency"]

        costs[y] = costs[y].rename(columns={"CO2 intensity": "co2_emissions"})

        costs[y].at["OCGT", "co2_emissions"] = costs[y].at["gas", "co2_emissions"]
        costs[y].at["CCGT", "co2_emissions"] = costs[y].at["gas", "co2_emissions"]

        costs[y].at["solar", "capital_cost"] = 0.5 * (
            costs[y].at["solar-rooftop", "capital_cost"]
            + costs[y].at["solar-utility", "capital_cost"]
        )

        def costs_for_storage(store, link1, link2=None, max_hours=1.0):
            capital_cost = link1["capital_cost"] + max_hours * store["capital_cost"]
            if link2 is not None:
                capital_cost += link2["capital_cost"]
            return pd.Series(
                dict(capital_cost=capital_cost, marginal_cost=0.0, co2_emissions=0.0)
            )

        max_hours = elec_config["max_hours"]
        costs[y].loc["battery"] = costs_for_storage(
            costs[y].loc["battery storage"],
            costs[y].loc["battery inverter"],
            max_hours=max_hours["battery"],
        )
        costs[y].loc['battery',:].fillna(costs[y].loc['battery inverter',:],inplace=True)
    
    for attr in ("marginal_cost", "capital_cost"):
        overwrites = config.get(attr)
        if overwrites is not None:
            overwrites = pd.Series(overwrites)
            costs[y].loc[overwrites.index, attr] = overwrites

    return costs

def add_generator_availability(n,generators,config_avail,eaf_projections):
    # Add plant availability based on actual Eskom data
    eskom_data  = pd.read_excel(snakemake.input.existing_generators_eaf, sheet_name='eskom_data', na_values=['-'],index_col=[1,0],parse_dates=True)
    snapshots = n.snapshots.get_level_values(1)
    years_full = range(n.investment_periods[0]-1,n.investment_periods[-1]+1)
    eaf_profiles = pd.DataFrame(1,index=snapshots,columns=[])   
    
    # All existing generators in the Eskom fleet with available data
    
    
    for tech in n.generators.index[n.generators.index.isin(eskom_data.index.get_level_values(0).unique())]:
        reference_data = eskom_data.loc[tech].loc[eskom_data.loc[tech].index.year.isin(config_avail['reference_years'])]
        base_eaf=(reference_data['EAF %']/100).groupby(reference_data['EAF %'].index.month).mean()
        carrier = n.generators.carrier[tech]
        for y in n.investment_periods:
            eaf_profiles.loc[str(y),tech]=base_eaf[eaf_profiles.loc[str(y)].index.month].values 
            if carrier + '_fleet_EAF' in eaf_projections.index:            
                eaf_profiles.loc[str(y),tech]=(base_eaf[eaf_profiles.loc[str(y)].index.month].values
                                *eaf_projections.loc[carrier+ '_fleet_EAF',y]
                                /(eskom_data.loc[carrier+'_total'].loc[eskom_data.loc[carrier+'_total'].index.year.isin(config_avail['reference_years']),'EAF %'].mean()/100))
                                                
                                            
    # New plants without existing data take best performing of Eskom fleet
    for carrier in ['coal', 'OCGT', 'CCGT', 'nuclear']:
        # 0 - Reference station, 1 - reference year, 2 - multiplier
        reference_data = eskom_data.loc[config_avail['new_unit_ref'][carrier][0]]
        reference_data = reference_data.loc[reference_data.index.year.isin(config_avail['new_unit_ref'][carrier][1])]
        base_eaf=(reference_data['EAF %']/100).groupby(reference_data['EAF %'].index.month).mean()* config_avail['new_unit_ref'][carrier][2]
        for gen_ext in n.generators[(n.generators.carrier==carrier) & (n.generators.p_nom_extendable)].index:
            eaf_profiles[gen_ext]=1
            for y in n.investment_periods:
                eaf_profiles.loc[str(y),gen_ext]=base_eaf[eaf_profiles.loc[str(y)].index.month].values
     
    eaf_profiles[eaf_profiles>1]=1
    eaf_profiles.index=n.snapshots
    n.generators_t.p_max_pu[eaf_profiles.columns]=eaf_profiles

def add_min_stable_levels(n, generators, config_min_stable):
    # Existing generators
    for gen in generators.index:
        if generators.loc[gen, "min_stable"] != 0:
            p_min_pu = n.generators_t.p_min_pu.get(gen, n.generators.p_max_pu[gen])
            n.generators_t.p_min_pu[gen] = p_min_pu * generators.loc[gen, "min_stable"]
            
            p_max_pu = n.generators_t.p_max_pu.get(gen, n.generators.p_max_pu[gen])
            if isinstance(p_max_pu, (pd.DataFrame, pd.Series)):
                n.generators_t.p_max_pu[gen] = p_max_pu.where(p_max_pu >= generators.loc[gen, "min_stable"], generators.loc[gen, "min_stable"])
            else:
                n.generators_t.p_max_pu[gen] = max(p_max_pu, generators.loc[gen, "min_stable"])

    # New plants without existing data take best performing of Eskom fleet
    for carrier in ["coal", "OCGT", "CCGT", "nuclear"]:
        for gen_ext in n.generators[(n.generators.carrier == carrier) & (~n.generators.index.isin(generators.index))].index:
            n.generators_t.p_min_pu[gen_ext] = n.generators_t.p_max_pu[gen_ext] * config_min_stable[carrier]

    n.generators_t.p_min_pu = n.generators_t.p_min_pu.fillna(0)

def add_partial_decommissioning(n, generators):
    # Only considered for existing conventional - partial decomissioning of capacity
    p_max_pu = get_as_dense(n, "Generator", "p_max_pu")
    p_min_pu = get_as_dense(n, "Generator", "p_min_pu")
    
    for tech in generators.index: #n.generators[n.generators.p_nom_extendable==False]
        for y in n.investment_periods:
            if y >= generators.decomdate_50[tech]:
                n.generators_t.p_max_pu.loc[y,tech] = 0.5*p_max_pu[tech]
                n.generators_t.p_min_pu.loc[y,tech] = 0.5*p_min_pu[tech]
    n.generators_t.p_min_pu=n.generators_t.p_min_pu.fillna(0)   


 ## Attach components
# ### Load

def attach_load(n, annual_demand):
    load = pd.read_csv(snakemake.input.load)
    
    annual_demand = annual_demand.drop('unit')*1e6

    load = load.set_index(
        pd.to_datetime(load['SETTLEMENT_DATE'] + ' ' +
                       load['PERIOD'].astype(str) + ':00')
        .rename('t'))['SYSTEMENERGY']

    demand=pd.Series(0,index=n.snapshots)
    
    profile_demand = normed(remove_leap_day(load.loc[str(snakemake.config['years']['reference_demand_year'])]))
    
    # if isinstance(n.snapshots, pd.MultiIndex):
    for y in n.investment_periods:
            demand.loc[y]=profile_demand.values*annual_demand[y]
    
    n.madd("Load", n.buses.index,
           bus=n.buses.index,
           p_set=pdbcast(demand, normed(n.buses.population)))


### Generate pu profiles for other_re based on Eskom data
def generate_eskom_profiles(n,config_carriers,ref_years):
    carriers= config_carriers + ['imports']
    if snakemake.config["enable"]["use_excel_wind_solar"][0]:
        carriers = [ elem for elem in carriers if elem not in ['onwind','solar']]

    eskom_data = (pd.read_csv(snakemake.input.eskom_profiles,skiprows=[1], 
                                index_col=0,parse_dates=True)
                                .resample('1h').mean())
    eskom_data  = remove_leap_day(eskom_data)
    eskom_profiles=pd.DataFrame(0,index=n.snapshots,columns=carriers)
    
    for carrier in carriers:
        weather_years=ref_years[carrier]
        for i in range(0,int(np.ceil(len(n.investment_periods)/len(weather_years))-1)):
            weather_years+=weather_years
        
        cnt=0
        # Use the default RSA hourly data (from Eskom) and extend to multiple weather years
        for y in n.investment_periods:    
            eskom_profiles.loc[y,carrier] = (eskom_data.loc[str(weather_years[cnt]),carrier]
                                        .clip(lower=0., upper=1.)).values     
            cnt+=1
    return eskom_profiles

def generate_excel_wind_solar_profiles(n,ref_years):
    profiles={}
    profiles['onwind'] = pd.DataFrame(0,index=n.snapshots,columns=n.buses.index)
    profiles['solar'] = pd.DataFrame(0,index=n.snapshots,columns=n.buses.index)
    # wind and solar resources can be explicitly specified in excel format
    for carrier in ['onwind','solar']:
        raw_profiles= (pd.read_excel(snakemake.config["enable"]["use_excel_wind_solar"][1],
                                    sheet_name=carrier+'_pu',
                                    skiprows=[1], 
                                    index_col=0,parse_dates=True)
                                    .resample('1h').mean())
        raw_profiles = remove_leap_day(raw_profiles)

        weather_years=ref_years[carrier]
        for i in range(0,int(np.ceil(len(n.investment_periods)/len(weather_years))-1)):
            weather_years+=weather_years
        
        cnt=0
        # Use the default RSA hourly data (from Eskom) and extend to multiple weather years
        for y in n.investment_periods:    
            profiles[carrier].loc[y,n.buses.index] = (raw_profiles.loc[str(weather_years[cnt]),n.buses.index]
                                        .clip(lower=0., upper=1.)).values     
            cnt+=1

    return profiles


### Set line costs

def update_transmission_costs(n, costs, length_factor=1.0, simple_hvdc_costs=False):
    for y in n.investment_periods:
        n.lines["capital_cost"] = (
            n.lines["length"] * length_factor * costs[y].at["HVAC overhead", "capital_cost"]
        )

        if n.links.empty:
            return

        dc_b = n.links.carrier == "DC"
        # If there are no "DC" links, then the 'underwater_fraction' column
        # may be missing. Therefore we have to return here.
        # TODO: Require fix
        if n.links.loc[n.links.carrier == "DC"].empty:
            return

        if simple_hvdc_costs:
            costs = (
                n.links.loc[dc_b, "length"]
                * length_factor
                * costs[y].at["HVDC overhead", "capital_cost"]
            )
        else:
            costs = (
                n.links.loc[dc_b, "length"]
                * length_factor
                * (
                    (1.0 - n.links.loc[dc_b, "underwater_fraction"])
                    * costs[y].at["HVDC overhead", "capital_cost"]
                    + n.links.loc[dc_b, "underwater_fraction"]
                    * costs[y].at["HVDC submarine", "capital_cost"]
                )
                + costs[y].at["HVDC inverter pair", "capital_cost"]
            )
        n.links.loc[dc_b, "capital_cost"] = costs


# ### Generators - TODO Update from pypa-eur
def attach_wind_and_solar(n, costs,input_profiles, model_setup, eskom_profiles):
    g_f, ps_f, csp_f = map_generator_parameters() 
    #input_files={'onwind_area': snakemake.input.onwind_area,
    #             'solar_area': snakemake.input.solar_area}

    # Aggregate existing REIPPPP plants per region
    eskom_gens = pd.read_excel(snakemake.input.model_file, 
                                    sheet_name='existing_eskom_stations', 
                                    na_values=['-'],
                                    index_col=[0,1]).loc[model_setup.existing_eskom_stations]
    eskom_gens = eskom_gens[eskom_gens['Carrier'].isin(['solar','onwind'])] # Currently only Sere wind farm
    ipp_gens = pd.read_excel(snakemake.input.model_file, 
                                    sheet_name='existing_non_eskom_stations', 
                                    na_values=['-'],
                                    index_col=[0,1]).loc[model_setup.existing_non_eskom_stations]

    ipp_gens=ipp_gens[ipp_gens['Carrier'].isin(['solar','onwind'])] # add existing wind and PV IPP generators 
    gens = pd.concat([eskom_gens,ipp_gens])
    gens['bus']=np.nan
    # Calculate fields where pypsa uses different conventions
    gens['marginal_cost'] = gens.pop(g_f['vom'])
    gens['capital_cost'] = 1e3*gens.pop(g_f['fom'])
    gens = gens.rename(columns={g_f[f]: f for f in {'p_nom', 'name', 'carrier', 'x', 'y','build_year','decomdate_100'}})
    gens['build_year'] = pd.to_datetime(gens['build_year'].fillna('{}-01-01'.format(n.investment_periods[0])).values).year 
    gens['decomdate_100'] = pd.to_datetime(gens['decomdate_100'].replace({'beyond 2050': '2051-01-01'}).values).year
    gens['lifetime'] = gens['decomdate_100'] - gens['build_year']
    gens = gens[gens.lifetime>0].drop(['decomdate_100','Status',
                                        g_f['maint_rate'],
                                        g_f['out_rate'],
                                        g_f['units'],
                                        g_f['unit_size'],
                                        g_f['min_stable']],axis=1)
    
    # Associate every generator with the bus of the region it is in or closest to
    pos = gpd.GeoSeries([Point(o.x, o.y) for o in gens[['x', 'y']].itertuples()], index=gens.index)
    regions = gpd.read_file(snakemake.input.supply_regions).set_index('name')
    for bus, region in regions.geometry.iteritems():
        pos_at_bus_b = pos.within(region)
        if pos_at_bus_b.any():
            gens.loc[pos_at_bus_b, "bus"] = bus
    gens.loc[gens.bus.isnull(), "bus"] = pos[gens.bus.isnull()].map(lambda p: regions.distance(p).idxmin())
    gens.loc['Sere','Grouping'] = 'REIPPPP_BW1' #add Sere wind farm to BW1 for simplification #TODO fix this to be general
    
    # Aggregate REIPPPP bid window generators at each bus #TODO use capacity weighted average for lifetime, costs 
    for carrier in ['solar','onwind']:
        plant_data = gens.loc[gens['carrier']==carrier,['Grouping','bus','p_nom']].groupby(['Grouping','bus']).sum()
        for param in ['lifetime','capital_cost','marginal_cost']:
            plant_data[param]=gens.loc[gens['carrier']==carrier,['Grouping','bus',param]].groupby(['Grouping','bus']).mean()
 
        resource_carrier=pd.DataFrame(0,index=n.snapshots,columns=n.buses.index) 
        if ((snakemake.config["enable"]["use_eskom_wind_solar"]==False) &
            (snakemake.config["enable"]["use_excel_wind_solar"][0]==False)):
            ds = xr.open_dataset(getattr(input_profiles, "profile_" + carrier))
            for y in n.investment_periods: 
                    atlite_data = ds["profile"].transpose("time", "bus").to_pandas()
                     #TODO remove hard coding only use 1yr from atlite at present
                    #resource_carrier.loc[y] = (atlite_data.loc[str(weather_years[cnt])].clip(lower=0., upper=1.)).values
                    resource_carrier.loc[y] = atlite_data.clip(lower=0., upper=1.).values[0:8760]     
        elif (snakemake.config["enable"]["use_excel_wind_solar"][0]):
            excel_wind_solar_profiles = generate_excel_wind_solar_profiles(n,
                                snakemake.config['years']['reference_weather_years'])    
            resource_carrier[n.buses.index] = excel_wind_solar_profiles[carrier][n.buses.index]
        else:
            for bus in n.buses.index:
                resource_carrier[bus] = eskom_profiles[carrier].values # duplicate aggregate Eskom profile if specified

        for group in plant_data.index.levels[0]:
            n.madd("Generator", plant_data.loc[group].index, suffix=" "+group+"_"+carrier,
                bus=plant_data.loc[group].index,
                carrier=carrier,
                build_year=n.investment_periods[0],
                lifetime=plant_data.loc[group,'lifetime'],
                p_nom = plant_data.loc[group,'p_nom'],
                p_nom_extendable=False,
                marginal_cost = plant_data.loc[group,'marginal_cost'],
                p_max_pu=resource_carrier[plant_data.loc[group].index],
                p_min_pu=resource_carrier[plant_data.loc[group].index]*0.95, # for existing PPAs force to buy all energy produced
                )
        
    # Add new generators
        for y in n.investment_periods:
            #TODO add check here to exclude buses where p_nom_max = 0 
            n.madd("Generator", n.buses.index, suffix=" "+carrier+"_"+str(y),
                bus=n.buses.index,
                carrier=carrier,
                build_year=y,
                lifetime=costs[y].at[carrier,'lifetime'],
                p_nom_extendable=True,
                #p_nom_max=ds["p_nom_max"].to_pandas(), # For multiple years a further constraint is applied in prepare_network.py
                #weight=ds["weight"].to_pandas(),
                marginal_cost=costs[y].at[carrier, 'marginal_cost'],
                capital_cost=costs[y].at[carrier, 'capital_cost'],
                efficiency=costs[y].at[carrier, 'efficiency'],
                p_max_pu=resource_carrier[n.buses.index])
    return gens
# # Generators
def attach_existing_generators(n, costs, eskom_profiles, model_setup):
    # Coal, gas, diesel, biomass, hydro, pumped storage
    g_f, ps_f, csp_f = map_generator_parameters()    

    # Add existing conventional generators that are active
    eskom_gens = pd.read_excel(snakemake.input.model_file, 
                                sheet_name='existing_eskom_stations',
                                na_values=['-'],
                                index_col=[0,1]).loc[model_setup.existing_eskom_stations]
    eskom_gens = eskom_gens[~eskom_gens['Carrier'].isin(['solar','onwind'])]
    ipp_gens = pd.read_excel(snakemake.input.model_file,
                                sheet_name='existing_non_eskom_stations',
                                na_values=['-'],
                                index_col=[0,1]).loc[model_setup.existing_non_eskom_stations]
    ipp_gens=ipp_gens[~ipp_gens['Carrier'].isin(['solar','onwind'])] # add existing non eskom generators (excluding solar, onwind)  
    gens = pd.concat([eskom_gens,ipp_gens])

    # Calculate fields where pypsa uses different conventions
    gens['efficiency'] = (3.6/gens.pop(g_f['heat_rate']))
    gens['marginal_cost'] = (3.6*gens.pop(g_f['fuel_price'])/gens['efficiency']).fillna(0) + gens.pop(g_f['vom'])
    gens['capital_cost'] = 1e3*gens.pop(g_f['fom'])
    gens['ramp_limit_up'] = 60*gens.pop(g_f['max_ramp_up'])/gens[g_f['p_nom']]
    gens['ramp_limit_down'] = 60*gens.pop(g_f['max_ramp_down'])/gens[g_f['p_nom']]

    gens = (gens.rename(columns={g_f[f]: f for f in {'p_nom', 'name', 'carrier', 'x', 'y','build_year','decomdate_50','decomdate_100','min_stable'}})
            .rename(columns={ps_f[f]: f for f in {'PHS_efficiency','PHS_max_hours'}})
            .rename(columns={csp_f[f]: f for f in {'CSP_max_hours'}}))

    gens['build_year'] = pd.to_datetime(gens['build_year'].fillna('{}-01-01'.format(n.investment_periods[0])).values).year 
    gens['decomdate_50'] = pd.to_datetime(gens['decomdate_50'].replace({'beyond 2050': '2051-01-01'}).values).year
    gens['decomdate_100'] = pd.to_datetime(gens['decomdate_100'].replace({'beyond 2050': '2051-01-01'}).values).year
    gens['lifetime'] = gens['decomdate_100'] - gens['build_year']
    gens['decomdate_50'] = gens['decomdate_50'].fillna(gens['decomdate_100'])
    gens = gens[gens.lifetime>0].drop(['decomdate_100','Status',g_f['maint_rate'],g_f['out_rate'],g_f['units'],g_f['unit_size']],axis=1)


    # CahoraBassa will be added later, even though we don't have coordinates
    CahoraBassa  = pd.DataFrame(gens.loc["CahoraBassa"]).T
    # Drop power plants where we don't have coordinates or capacity
    gens = pd.DataFrame(gens.loc[lambda df: (df.p_nom>0.) & df.x.notnull() & df.y.notnull()])

    # Associate every generator with the bus of the region it is in or closest to
    pos = gpd.GeoSeries([Point(o.x, o.y) for o in gens[['x', 'y']].itertuples()], index=gens.index)
    regions = gpd.read_file(snakemake.input.supply_regions).set_index('name')
    for bus, region in regions.geometry.iteritems():
        pos_at_bus_b = pos.within(region)
        if pos_at_bus_b.any():
            gens.loc[pos_at_bus_b, "bus"] = bus
    gens.loc[gens.bus.isnull(), "bus"] = pos[gens.bus.isnull()].map(lambda p: regions.distance(p).idxmin())

    if snakemake.wildcards.regions=='RSA':
        CahoraBassa['bus'] = "RSA"
    elif (snakemake.wildcards.regions=='9-supply') | (snakemake.wildcards.regions=='10-supply'):
        CahoraBassa['bus'] = "LIMPOPO"
    elif snakemake.wildcards.regions=='27-supply':
        CahoraBassa['bus'] = "POLOKWANE"
    gens = pd.concat([gens,CahoraBassa])

    gen_index=gens[gens.carrier.isin(['coal','nuclear','gas','diesel','hydro'])].index
    n.madd("Generator", gen_index,
        bus=gens.loc[gen_index,'bus'],
        carrier=gens.loc[gen_index,'carrier'],
        build_year=n.investment_periods[0],
        lifetime=gens.loc[gen_index,'lifetime'],
        p_nom = gens.loc[gen_index,'p_nom'],
        p_nom_extendable=False,
        ramp_limit_up = gens.loc[gen_index,'ramp_limit_up'],
        ramp_limit_down = gens.loc[gen_index,'ramp_limit_down'],
        marginal_cost=gens.loc[gen_index,'marginal_cost'],
        capital_cost=gens.loc[gen_index,'capital_cost'],
        #p_max_pu - added later under generator availability function
        )  
 
    for carrier in ['CSP','biomass']:
        n.add("Carrier", name=carrier)
        plant_data = gens.loc[gens['carrier']==carrier,['Grouping','bus','p_nom']].groupby(['Grouping','bus']).sum()
        for param in ['lifetime','capital_cost','marginal_cost']:
            plant_data[param]=gens.loc[gens['carrier']==carrier,['Grouping','bus',param]].groupby(['Grouping','bus']).mean()

        for group in plant_data.index.levels[0]:
            # Duplicate Aggregate Eskom Data across the regions
            eskom_data = pd.concat([eskom_profiles[carrier]] * (len(plant_data.loc[group].index)), axis=1, ignore_index=True)
            eskom_data.columns = plant_data.loc[group].index
            capacity_factor = (eskom_data[plant_data.loc[group].index]).mean()[0]
            annual_cost = capacity_factor * 8760 * plant_data.loc[group,'marginal_cost']

            n.madd("Generator", plant_data.loc[group].index, suffix=" "+group+"_"+carrier,
                bus=plant_data.loc[group].index,
                carrier=carrier,
                build_year=n.investment_periods[0],
                lifetime=plant_data.loc[group,'lifetime'],
                p_nom = plant_data.loc[group,'p_nom'],
                p_nom_extendable=False,
                capital_cost=annual_cost,
                p_max_pu=eskom_data.values,
                p_min_pu=eskom_data.values*0.95, #purchase at least 95% of power under existing PPAs despite higher cost
                )    

    # ## HYDRO and PHS    
    # # Cohora Bassa imports to South Africa - based on Actual Eskom data from 2017-2022
    n.generators_t.p_max_pu['CahoraBassa'] = eskom_profiles['imports'].values
    # Hydro power generation - based on actual Eskom data from 2017-2022
    for tech in n.generators[n.generators.carrier=='hydro'].index:
        n.generators_t.p_max_pu[tech] = eskom_profiles['hydro'].values
    
    # PHS
    phs = gens[gens.carrier=='PHS']
    n.madd('StorageUnit', phs.index, carrier='PHS',
            bus=phs['bus'],
            p_nom=phs['p_nom'],
            max_hours=phs['PHS_max_hours'],
            capital_cost=phs['capital_cost'],
            marginal_cost=phs['marginal_cost'],
            efficiency_dispatch=phs['PHS_efficiency']**(0.5),
            efficiency_store=phs['PHS_efficiency']**(0.5),
            cyclic_state_of_charge=True
            #inflow=inflow_t.loc[:, hydro.index]) #TODO add in
            )

    _add_missing_carriers_from_costs(n, costs[n.investment_periods[0]], gens.carrier.unique())

    return gens

def attach_extendable_generators(n, costs):
    elec_opts = snakemake.config['electricity']
    carriers = elec_opts['extendable_carriers']['Generator']
    if snakemake.wildcards.regions=='RSA':
        buses = dict(zip(carriers,['RSA']*len(carriers)))
    elif snakemake.wildcards.regions=='27-supply':
        buses = elec_opts['buses']['27-supply']
    else:
        buses = elec_opts['buses']['9_10-supply']

    _add_missing_carriers_from_costs(n, costs[n.investment_periods[0]], carriers)

    for y in n.investment_periods: 
        for carrier in carriers:
            buses_i = buses.get(carrier, n.buses.index)
            if buses_i=='RSA':
                n.add("Generator", buses_i + " " + carrier + "_"+str(y),
                    bus=buses_i,
                    p_nom_extendable=True,
                    carrier=carrier,
                    build_year=y,
                    lifetime=costs[y].at[carrier, 'lifetime'],
                    capital_cost=costs[y].at[carrier, 'capital_cost'],
                    marginal_cost=costs[y].at[carrier, 'marginal_cost'],
                    efficiency=costs[y].at[carrier, 'efficiency'])
            else:
                n.madd("Generator", buses_i, suffix=" " + carrier + "_"+str(y),
                    bus=buses_i,
                    p_nom_extendable=True,
                    carrier=carrier,
                    build_year=y,
                    lifetime=costs[y].at[carrier, 'lifetime'],
                    capital_cost=costs[y].at[carrier, 'capital_cost'],
                    marginal_cost=costs[y].at[carrier, 'marginal_cost'],
                    efficiency=costs[y].at[carrier, 'efficiency'])


def attach_storage(n, costs):
    elec_opts = snakemake.config['electricity']
    carriers = elec_opts['extendable_carriers']['StorageUnit']
    max_hours = elec_opts['max_hours']
    buses = elec_opts['buses']

    _add_missing_carriers_from_costs(n, costs[n.investment_periods[0]], carriers)

    for y in n.investment_periods:
        for carrier in carriers:
            buses_i = buses.get(carrier, n.buses.index)
            n.madd("StorageUnit", buses_i, " " + carrier + "_" + str(y),
                bus=buses_i,
                p_nom_extendable=True,
                carrier=carrier,
                build_year=y,
                capital_cost=costs[y].at[carrier, 'capital_cost'],
                marginal_cost=costs[y].at[carrier, 'marginal_cost'],
                efficiency_store=costs[y].at[carrier, 'efficiency_store'],
                efficiency_dispatch=costs[y].at[carrier, 'efficiency_dispatch'],
                max_hours=max_hours[carrier],
                cyclic_state_of_charge=True)

def add_co2limit(n):
    n.add("GlobalConstraint", "CO2Limit",
          carrier_attribute="co2_emissions", sense="<=",
          constant=snakemake.config['electricity']['co2limit'])

def add_emission_prices(n, emission_prices=None, exclude_co2=False):
    if emission_prices is None:
        emission_prices = snakemake.config['costs']['emission_prices']
    if exclude_co2: emission_prices.pop('co2')
    ep = (pd.Series(emission_prices).rename(lambda x: x+'_emissions') * n.carriers).sum(axis=1)
    n.generators['marginal_cost'] += n.generators.carrier.map(ep)
    n.storage_units['marginal_cost'] += n.storage_units.carrier.map(ep)

def add_peak_demand_hour_without_variable_feedin(n):
    new_hour = n.snapshots[-1] + pd.Timedelta(hours=1)
    n.set_snapshots(n.snapshots.append(pd.Index([new_hour])))

    # Don't value new hour for energy totals
    n.snapshot_weightings[new_hour] = 0.

    # Don't allow variable feed-in in this hour
    n.generators_t.p_max_pu.loc[new_hour] = 0.

    n.loads_t.p_set.loc[new_hour] = (
        n.loads_t.p_set.loc[n.loads_t.p_set.sum(axis=1).idxmax()]
        * (1.+snakemake.config['electricity']['SAFE_reservemargin'])
    )

def add_nice_carrier_names(n, config):
    carrier_i = n.carriers.index
    nice_names = (
        pd.Series(config["plotting"]["nice_names"])
        .reindex(carrier_i)
        .fillna(carrier_i.to_series().str.title())
    )
    n.carriers["nice_name"] = nice_names
    colors = pd.Series(config["plotting"]["tech_colors"]).reindex(carrier_i)
    if colors.isna().any():
        missing_i = list(colors.index[colors.isna()])
        logger.warning(
            f"tech_colors for carriers {missing_i} not defined " "in config."
        )
    n.carriers["color"] = colors


if __name__ == "__main__":
    if 'snakemake' not in globals():
        from _helpers import mock_snakemake
        snakemake = mock_snakemake('add_electricity', 
                        **{'model_file':'CSIR-ambitions-2022',
                            'regions':'RSA',
                            'resarea':'redz',
                            'll':'copt',
                            'attr':'p_nom'})

    model_setup = (pd.read_excel(snakemake.input.model_file, 
                                sheet_name='model_setup',
                                index_col=[0])
                                .loc[snakemake.wildcards.model_file])

    projections = (pd.read_excel(snakemake.input.model_file, 
                            sheet_name='projected_parameters',
                            index_col=[0,1])
                            .loc[model_setup['projected_parameters']])

    #opts = snakemake.wildcards.opts.split('-')
    n = pypsa.Network(snakemake.input.base_network)
    costs = load_costs(
        snakemake.input.model_file,
        model_setup.costs,
        snakemake.config["costs"],
        snakemake.config["electricity"],
        snakemake.config["years"]["simulation"],
    )

    #wind_solar_profiles = xr.open_dataset(snakemake.input.wind_solar_profiles).to_dataframe()
    eskom_profiles = generate_eskom_profiles(n,
                        snakemake.config['electricity']['renewable_carriers'],
                        snakemake.config['years']['reference_weather_years'])

    attach_load(n, projections.loc['annual_demand',:])
    if snakemake.wildcards.regions!='RSA':
        update_transmission_costs(n, costs)
    gens = attach_existing_generators(n, costs, eskom_profiles, model_setup)
    attach_wind_and_solar(n, costs, snakemake.input, model_setup, eskom_profiles)
    attach_extendable_generators(n, costs)
    attach_storage(n, costs)
    if snakemake.config['electricity']['generator_availability']['implement_availability']==True:
        add_generator_availability(n,
                    gens,
                    snakemake.config['electricity']['generator_availability'],
                    projections)
        add_min_stable_levels(n,gens,snakemake.config['electricity']['min_stable_levels'])    
    add_partial_decommissioning(n,gens[gens.carrier=='coal'])      
    add_nice_carrier_names(n, snakemake.config)
    n.export_to_netcdf(snakemake.output[0])

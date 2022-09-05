# coding: utf-8

import networkx as nx
import pandas as pd
import numpy as np
import scipy as sp
from operator import attrgetter
from six import string_types

import rasterio
import fiona
import rasterstats
import geopandas as gpd

from shapely.geometry import Point
from vresutils.shapes import haversine
from vresutils.costdata import annuity

import pypsa

from _helpers import pdbcast

def normed(s): return s/s.sum()

def _add_missing_carriers_from_costs(n, costs, carriers):
    missing_carriers = pd.Index(carriers).difference(n.carriers.index)
    emissions_cols = costs.columns.to_series().loc[lambda s: s.str.endswith('_emissions')].values
    n.import_components_from_dataframe(costs.loc[missing_carriers, emissions_cols].fillna(0.), 'Carrier')

def load_costs():
    costs = pd.read_excel(snakemake.input.tech_costs,
                          sheet_name=snakemake.wildcards.cost,
                          index_col=0).T

    discountrate = snakemake.config['costs']['discountrate']
    costs['capital_cost'] = ((annuity(costs.pop('Lifetime [a]'), discountrate) +
                              costs.pop('FOM [%/a]').fillna(0.) / 100.)
                             * costs.pop('Overnight cost [R/kW_el]')*1e3)

    costs['efficiency'] = costs.pop('Efficiency').fillna(1.)
    costs['marginal_cost'] = (costs.pop('VOM [R/MWh_el]').fillna(0.) +
                              (costs.pop('Fuel cost [R/MWh_th]') / costs['efficiency']).fillna(0.))

    emissions_cols = costs.columns.to_series().loc[lambda s: s.str.endswith(' emissions [kg/MWh_th]')]
    costs.loc[:, emissions_cols.index] = (costs.loc[:, emissions_cols.index]/1e3).fillna(0.)
    costs = costs.rename(columns=emissions_cols.str[:-len(" [kg/MWh_th]")].str.lower().str.replace(' ', '_'))

    for attr in ('marginal_cost', 'capital_cost'):
        overwrites = snakemake.config['costs'].get(attr)
        if overwrites is not None:
            overwrites = pd.Series(overwrites)
            costs.loc[overwrites.index, attr] = overwrites

    return costs

# ## Attach components

# ### Load

def attach_load(n):
    load = pd.read_csv(snakemake.input.load)
    load = load.set_index(
        pd.to_datetime(load['SETTLEMENT_DATE'] + ' ' +
                       load['PERIOD'].astype(str) + ':00')
        .rename('t')
    )['SYSTEMENERGY']

    demand = (snakemake.config['electricity']['demand'] *
              normed(load.loc[snakemake.config['historical_year']]))
    n.madd("Load", n.buses.index,
           bus=n.buses.index,
           p_set=pdbcast(demand, normed(n.buses.population)))

### Set line costs

def update_transmission_costs(n, costs):
    opts = snakemake.config['lines']
    for df in (n.lines, n.links):
        if df.empty: continue

        df['capital_cost'] = (df['length'] / opts['s_nom_factor'] *
                              costs.at['Transmission lines', 'capital_cost'])


# ### Generators

def attach_wind_and_solar(n, costs):
    historical_year = snakemake.config['historical_year']
    capacity_per_sqm = snakemake.config['respotentials']['capacity_per_sqm']

    ## Wind

    n.add("Carrier", name="Wind")
    windarea = pd.read_csv(snakemake.input.wind_area, index_col=0).loc[lambda s: s.available_area > 0.]
    windres = (pd.read_excel(snakemake.input.wind_profiles,
                             skiprows=[1], sheet_name='Wind power profiles')
               .rename(columns={'supply area\'s name': 't'}).set_index('t')
               .resample('1h').mean().loc[historical_year]
               .reindex(columns=windarea.index)
               .clip(lower=0., upper=1.))
    n.madd("Generator", windarea.index, suffix=" Wind",
           bus=windarea.index,
           carrier="Wind",
           p_nom_extendable=True,
           p_nom_max=windarea.available_area * capacity_per_sqm['wind'],
           marginal_cost=costs.at['Wind', 'marginal_cost'],
           capital_cost=costs.at['Wind', 'capital_cost'],
           efficiency=costs.at['Wind', 'efficiency'],
           p_max_pu=windres)

    ## PV

    n.add("Carrier", name="PV")
    pvarea = pd.read_csv(snakemake.input.solar_area, index_col=0).loc[lambda s: s.available_area > 0.]
    pvres = (pd.read_excel(snakemake.input.pv_profiles,
                           skiprows=[1], sheet_name='PV profiles')
             .rename(columns={'supply area\'s name': 't'})
             .set_index('t')
             .resample('1h').mean().loc[historical_year].reindex(n.snapshots, fill_value=0.)
             .reindex(columns=pvarea.index)
             .clip(lower=0., upper=1.))
    n.madd("Generator", pvarea.index, suffix=" PV",
           bus=pvarea.index,
           carrier="PV",
           p_nom_extendable=True,
           p_nom_max=pvarea.available_area * capacity_per_sqm['solar'],
           marginal_cost=costs.at['PV', 'marginal_cost'],
           capital_cost=costs.at['PV', 'capital_cost'],
           efficiency=costs.at['PV', 'efficiency'],
           p_max_pu=pvres)


# # Generators


def attach_existing_generators(n, costs):
    historical_year = snakemake.config['historical_year']

    ps_f = dict(efficiency="Pump Efficiency (%)",
                pump_units="Pump Units",
                pump_load="Pump Load per unit (MW)",
                max_storage="Pumped Storage - Max Storage (GWh)")

    csp_f = dict(max_hours='CSP Storage (hours)')

    g_f = dict(fom="Fixed Operations and maintenance costs (R/kW/yr)",
               p_nom='Installed/ Operational Capacity in 2016 (MW)',
               name='Power Station Name',
               carrier='Fuel/technology type',
               decomdate='Decommissioning Date',
               x='GPS Longitude',
               y='GPS Latitude',
               status='Status',
               heat_rate='Heat Rate (GJ/MWh)',
               fuel_price='Fuel Price (R/GJ)',
               vom='Variable Operations and Maintenance Cost (R/MWh)',
               max_ramp_up='Max Ramp Up (MW/min)',
               unit_size='Unit size (MW)',
               units='Number units',
               maint_rate='Typical annual maintenance rate (%)',
               out_rate='Typical annual forced outage rate (%)',
               owner='Owner')

    gens = pd.read_excel(snakemake.input.existing_generators, na_values=['-'])

    # Make field "Fixed Operations and maintenance costs" numeric
    includescapex_i = gens[g_f['fom']].str.endswith(' (includes capex)').dropna().index
    gens.loc[includescapex_i, g_f['fom']] = gens.loc[includescapex_i, g_f['fom']].str[:-len(' (includes capex)')]
    gens[g_f['fom']] = pd.to_numeric(gens[g_f['fom']])


    # Calculate fields where pypsa uses different conventions
    gens['efficiency'] = 3.6/gens.pop(g_f['heat_rate'])
    gens['marginal_cost'] = 3.6*gens.pop(g_f['fuel_price'])/gens['efficiency'] + gens.pop(g_f['vom'])
    gens['capital_cost'] = 1e3*gens.pop(g_f['fom'])
    gens['ramp_limit_up'] = 60*gens.pop(g_f['max_ramp_up'])/gens[g_f['p_nom']]

    year = snakemake.config['year']
    gens = (gens
            # rename remaining fields
            .rename(columns={g_f[f]: f
                             for f in {'p_nom', 'name', 'carrier', 'x', 'y'}})
            # remove all power plants decommissioned before 2030
            .loc[lambda df: ((pd.to_datetime(df[g_f['decomdate']].replace({'beyond 2050': np.nan}).dropna()) >= year)
                                .reindex(df.index, fill_value=True))]
            # drop unused fields
            .drop([g_f[f] for f in {'unit_size', 'units', 'maint_rate',
                                    'out_rate', 'decomdate', 'status'}], axis=1)
    ).set_index('name')

    # CahoraBassa will be added later, even though we don't have coordinates
    CahoraBassa = gens.loc["CahoraBassa"]

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
    elif snakemake.wildcards.regions=='27-supply':
        CahoraBassa['bus'] = "POLOKWANE"
    gens = gens.append(CahoraBassa)

    # Now we split them by carrier and have some more carrier specific cleaning
    gens.carrier.replace({"Pumped Storage": "Pumped storage"}, inplace=True)

    # HYDRO

    n.add("Carrier", "Hydro")
    n.add("Carrier", "Pumped storage")

    hydro = pd.DataFrame(gens.loc[gens.carrier.isin({'Pumped storage', 'Hydro'})])
    hydro["efficiency_store"] = hydro["efficiency_dispatch"] = np.sqrt(hydro.pop(ps_f['efficiency'])/100.).fillna(1.)

    hydro["max_hours"] = 1e3*hydro.pop(ps_f["max_storage"])/hydro["p_nom"]

    hydro["p_min_pu"] = - (hydro.pop(ps_f["pump_load"]) * hydro.pop(ps_f["pump_units"]) / hydro["p_nom"]).fillna(0.)

    hydro = (hydro
             .assign(p_max_pu=1.0, cyclic_state_of_charge=True)
             .drop(list(csp_f.values()) + ['ramp_limit_up', 'efficiency'], axis=1))

    hydro.max_hours.fillna(hydro.max_hours.mean(), inplace=True)

    hydro_inflow = pd.read_csv(snakemake.input.hydro_inflow, index_col=0, parse_dates=True).loc[historical_year]
    hydro_za_b = (hydro.index.to_series() != 'CahoraBassa')
    hydro_inflow_za = pd.DataFrame(hydro_inflow[['ZA']].values * normed(hydro.loc[hydro_za_b, 'p_nom'].values),
                                   columns=hydro.index[hydro_za_b], index=hydro_inflow.index)
    hydro_inflow_za['CahoraBassa'] = hydro.at['CahoraBassa', 'p_nom']/2187.*hydro_inflow['MZ']

    hydro.marginal_cost.fillna(0., inplace=True)
    n.import_components_from_dataframe(hydro, "StorageUnit")
    n.import_series_from_dataframe(hydro_inflow_za, "StorageUnit", "inflow")

    if snakemake.config['electricity'].get('csp'):
        n.add("Carrier", "CSP")

        csp = (pd.DataFrame(gens.loc[gens.carrier == "CSP"])
               .drop(list(ps_f.values()) + ["ramp_limit_up", "efficiency"], axis=1)
               .rename(columns={csp_f['max_hours']: 'max_hours'}))

        # TODO add to network with time-series and everything

    gens = (gens.loc[gens.carrier.isin({"Coal", "Nuclear"})]
            .drop(list(ps_f.values()) + list(csp_f.values()), axis=1))
    _add_missing_carriers_from_costs(n, costs, gens.carrier.unique())
    n.import_components_from_dataframe(gens, "Generator")

def attach_extendable_generators(n, costs):
    elec_opts = snakemake.config['electricity']
    carriers = elec_opts['extendable_carriers']['Generator']
    if snakemake.wildcards.regions=='RSA':
        buses=['RSA']
    elif snakemake.wildcards.regions=='27-supply':
        buses = elec_opts['buses']

    _add_missing_carriers_from_costs(n, costs, carriers)

    for carrier in carriers:
        buses_i = buses.get(carrier, n.buses.index)
        n.madd("Generator", buses_i, suffix=" " + carrier,
               bus=buses_i,
               p_nom_extendable=True,
               carrier=carrier,
               capital_cost=costs.at[carrier, 'capital_cost'],
               marginal_cost=costs.at[carrier, 'marginal_cost'],
               efficiency=costs.at[carrier, 'efficiency'])


def attach_storage(n, costs):
    elec_opts = snakemake.config['electricity']
    carriers = elec_opts['extendable_carriers']['StorageUnit']
    max_hours = elec_opts['max_hours']
    buses = elec_opts['buses']

    _add_missing_carriers_from_costs(n, costs, carriers)

    for carrier in carriers:
        buses_i = buses.get(carrier, n.buses.index)
        n.madd("StorageUnit", buses_i, " " + carrier,
               bus=buses_i,
               p_nom_extendable=True,
               carrier=carrier,
               capital_cost=costs.at[carrier, 'capital_cost'],
               marginal_cost=costs.at[carrier, 'marginal_cost'],
               efficiency_store=costs.at[carrier, 'efficiency'],
               efficiency_dispatch=costs.at[carrier, 'efficiency'],
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



if __name__ == "__main__":
    opts = snakemake.wildcards.opts.split('-')
    n = pypsa.Network(snakemake.input.base_network)
    costs = load_costs()
    attach_load(n)
    update_transmission_costs(n, costs)
    attach_existing_generators(n, costs)
    attach_wind_and_solar(n, costs)
    attach_extendable_generators(n, costs)
    attach_storage(n, costs)

    if 'Co2L' in opts:
        add_co2limit(n)
        add_emission_prices(n, exclude_co2=True)

    if 'Ep' in opts:
        add_emission_prices(n)

    if 'SAFE' in opts:
        add_peak_demand_hour_without_variable_feedin(n)

    n.export_to_netcdf(snakemake.output[0])

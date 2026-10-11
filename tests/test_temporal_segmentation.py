# SPDX-FileCopyrightText:  PyPSA-RSA Authors
# SPDX-License-Identifier: MIT

"""Regression tests for the defects recorded in ``docs/known_issues.rst``.

These are unit tests over the helpers and the network-cleanup pass; they do not
build or solve a network. Each test fails against the pre-fix code, so it
documents both the defect and the fix.
"""

import sys
from pathlib import Path

import pandas as pd
import pypsa
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from _helpers import assign_segmented_df_to_network, normalize_and_rename_df  # noqa: E402


@pytest.fixture
def snapshots():
    return pd.date_range("2030-01-01", periods=4, freq="h")


@pytest.fixture
def network(snapshots):
    n = pypsa.Network()
    n.set_snapshots(snapshots)
    n.add("Bus", "RSA", v_nom=400.0)
    return n


def test_normalize_returns_the_pre_division_maximum(snapshots):
    """The maxima are needed to restore absolute magnitudes after aggregation."""
    load = pd.DataFrame({"A": [10.0, 20.0, 30.0, 40.0]}, index=snapshots)

    normalised, maxima = normalize_and_rename_df(load, snapshots, 1, "load")

    assert normalised["A_load"].max() == 1.0
    assert maxima["A_load"] == 40.0
    # The maxima index must track the rename so the caller can de-normalise.
    assert list(maxima.index) == ["A_load"]


def test_denormalisation_round_trip_restores_absolute_magnitudes(snapshots):
    """Regression: the returned maxima used to be 1.0, so this restored nothing."""
    load = pd.DataFrame({"A": [10.0, 20.0, 30.0, 40.0]}, index=snapshots)
    _, maxima = normalize_and_rename_df(load, snapshots, 1, "load")

    segmented = pd.DataFrame({"A_load": [0.25, 0.75]}, index=snapshots[:2])
    segmented[list(maxima.index)] *= maxima

    assert segmented["A_load"].tolist() == [10.0, 30.0]


def test_assign_segmented_df_writes_through_to_the_network(network):
    """Regression: the function used to rebind a local name and change nothing.

    Mirrors the call order in ``apply_time_segmentation``: the snapshot axis is
    shrunk to the segments before the profiles are written back.
    """
    network.add("Load", "L1", bus="RSA", p_set=10.0)
    segmented_index = pd.DatetimeIndex(["2030-01-01", "2030-01-02"])
    network.set_snapshots(segmented_index)
    segmented = pd.DataFrame({"L1_load": [5.0, 6.0]}, index=segmented_index)

    assign_segmented_df_to_network(segmented, "_load", "", network.loads_t.p_set)

    assert network.loads_t.p_set["L1"].tolist() == [5.0, 6.0]



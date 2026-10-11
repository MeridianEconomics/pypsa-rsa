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


def _committable_network(snapshots, **generator_kwargs):
    n = pypsa.Network()
    n.set_snapshots(snapshots)
    n.add("Bus", "RSA", v_nom=400.0)
    n.add("Generator", "coal1", bus="RSA", carrier="coal", p_nom=100.0,
          committable=True, p_max_pu=0.9, **generator_kwargs)
    n.generators_t.p_max_pu = pd.DataFrame(0.9, index=snapshots, columns=["coal1"])
    n.generators_t.p_min_pu = pd.DataFrame(
        generator_kwargs.get("p_min_pu", 0.0), index=snapshots, columns=["coal1"]
    )
    return n


def test_ramp_cleanup_repairs_the_static_attribute(snapshots):
    """Regression: the repair wrote a dynamic frame that PyPSA never reads.

    ``ramp_limit_start_up`` and ``ramp_limit_shut_down`` are static floats; the
    committable constraint reads them from ``n.generators``.
    """
    from add_electricity import check_pu_profiles

    n = _committable_network(
        snapshots, p_min_pu=0.65, ramp_limit_start_up=0.1,
        ramp_limit_shut_down=0.1, ramp_limit_up=0.1, ramp_limit_down=0.01,
    )

    check_pu_profiles(n, clean_flag=True)

    # Raised to the minimum stable level, in the place PyPSA reads.
    assert n.generators.ramp_limit_start_up["coal1"] == pytest.approx(0.65)
    assert n.generators.ramp_limit_shut_down["coal1"] == pytest.approx(0.65)
    # And no stray dynamic attribute was created for a static field.
    assert "ramp_limit_shut_down" not in n.generators_t
    assert "ramp_limit_start_up" not in n.generators_t


def test_ramp_cleanup_clamps_ramp_limit_down(snapshots):
    """Regression: the clamp was written to ``ramp_limit_shut_down`` instead."""
    from add_electricity import check_pu_profiles

    n = _committable_network(
        snapshots, p_min_pu=0.0, ramp_limit_start_up=0.65,
        ramp_limit_shut_down=0.65, ramp_limit_up=0.1, ramp_limit_down=0.01,
    )

    check_pu_profiles(n, clean_flag=True)

    assert n.generators_t.ramp_limit_down["coal1"].max() == pytest.approx(0.05)


def test_ramp_cleanup_is_a_noop_when_nothing_is_committable(snapshots):
    """The cleanup only applies to committable units, as PyPSA does."""
    from add_electricity import check_pu_profiles

    n = pypsa.Network()
    n.set_snapshots(snapshots)
    n.add("Bus", "RSA", v_nom=400.0)
    n.add("Generator", "g1", bus="RSA", carrier="coal", p_nom=100.0,
          committable=False, p_min_pu=0.0, ramp_limit_start_up=0.1,
          ramp_limit_shut_down=0.1, ramp_limit_up=0.1, ramp_limit_down=0.01)
    n.generators_t.p_max_pu = pd.DataFrame(0.9, index=snapshots, columns=["g1"])
    n.generators_t.p_min_pu = pd.DataFrame(0.0, index=snapshots, columns=["g1"])

    check_pu_profiles(n, clean_flag=True)

    assert n.generators.ramp_limit_start_up["g1"] == pytest.approx(0.1)

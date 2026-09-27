import pytest

from btmesh.access import AccessError
from sphere_mesh.access import config_network_transmit_set


def test_network_transmit_set_matches_sphere_configuration():
    assert config_network_transmit_set(5, 2) == bytes.fromhex("802415")


@pytest.mark.parametrize("count", [-1, 8])
def test_network_transmit_set_rejects_invalid_count(count):
    with pytest.raises(AccessError, match="count"):
        config_network_transmit_set(count, 2)


@pytest.mark.parametrize("steps", [-1, 32])
def test_network_transmit_set_rejects_invalid_interval(steps):
    with pytest.raises(AccessError, match="interval"):
        config_network_transmit_set(5, steps)

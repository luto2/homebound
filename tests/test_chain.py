"""Chain resolution: connected components, the k-space radius and tree rows."""
import pytest

from conftest import HOME, UNIVERSE, MapSimulation, pick_system
from homebound.mapping.chain import connected_component, resolve_chain, wormhole_adjacency


def test_connected_component() -> None:
    """Everything reachable is in the component, nothing else."""
    adjacency = {1: {2}, 2: {1, 3}, 3: {2}, 4: {5}, 5: {4}}
    assert connected_component(adjacency, 1) == {1, 2, 3}
    assert connected_component(adjacency, 9) == {9}
    with pytest.raises(TypeError):
        connected_component([(1, 2)], 1)


def test_known_space_radius(simulation) -> None:
    """A k-space character up to 5 gate jumps from an entrance is in the chain, not at 6."""
    jita = UNIVERSE.by_name("Jita")
    simulation.jump(jita.name)  # home -> Jita by WH; Jita is the entrance
    distances = UNIVERSE.gate_distances(jita.id, 6)
    for jumps in range(0, 7):
        system_id = next(candidate for candidate, distance in sorted(distances.items()) if distance == jumps)
        chain = resolve_chain(simulation.store, UNIVERSE, system_id, HOME.id, radius=5)
        if jumps <= 5:
            assert chain is not None and HOME.id in chain.members, jumps
            if jumps:
                assert chain.entrance_id == jita.id and chain.jumps == jumps
                assert any(row.virtual and row.current and row.jumps_from == jumps for row in chain.rows)
        else:
            assert chain is None


def test_tree_rows_have_box_drawing_prefixes(simulation) -> None:
    """Rows follow the naming tree, with └/├ prefixes and the current row marked."""
    c5 = pick_system("C5", exclude={HOME.id})
    simulation.tracker.apply_naming(simulation.naming_request(simulation.jump(c5.name)), is_static=True)
    simulation.jump(HOME.name)
    c3 = pick_system("C3")
    simulation.jump(c3.name)
    chain = resolve_chain(simulation.store, UNIVERSE, c3.id, HOME.id)
    assert [row.system_id for row in chain.rows] == [HOME.id, c5.id, c3.id]
    assert [row.prefix for row in chain.rows] == ["", "├ ", "└ "]
    assert chain.rows[2].current


def test_deep_rows_fold_and_root_without_home() -> None:
    """Rows past max_depth get a … prefix; without home the topmost parent is the root."""
    simulation = MapSimulation()
    systems = [pick_system(system_class) for system_class in ("C1", "C2", "C3")]
    previous = HOME.id
    for system in systems:
        simulation.tracker.add_wormhole(previous, system.id)
        previous = system.id
    chain = resolve_chain(simulation.store, UNIVERSE, systems[-1].id, None, max_depth=1)
    assert chain.root_id == HOME.id
    assert chain.rows[-1].prefix.endswith("… ")


def test_no_chain_cases(simulation) -> None:
    """No location, an unmapped w-space system or a far k-space system give no chain."""
    assert resolve_chain(simulation.store, UNIVERSE, None) is None
    assert resolve_chain(simulation.store, UNIVERSE, pick_system("C2").id) is None
    assert resolve_chain(simulation.store, UNIVERSE, UNIVERSE.by_name("Jita").id) is None
    with pytest.raises(ValueError):
        resolve_chain(simulation.store, UNIVERSE, HOME.id, radius=-1)


def test_wormhole_adjacency_is_symmetric(simulation) -> None:
    """Each connection shows up from both ends."""
    c5 = pick_system("C5", exclude={HOME.id})
    simulation.tracker.add_wormhole(HOME.id, c5.id)
    assert wormhole_adjacency(simulation.store) == {HOME.id: {c5.id}, c5.id: {HOME.id}}
    with pytest.raises(TypeError):
        wormhole_adjacency(None)

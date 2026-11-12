"""Chain resolution: connected components, the k-space radius and tree rows."""
import pytest

from conftest import HOME, UNIVERSE, MapSimulation, pick_system
from homebound.mapping.chain import connected_component, focus_rows, resolve_chain, wormhole_adjacency


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

    def chain_at(jumps: int):
        """Resolve the chain for a system exactly ``jumps`` gate jumps from Jita."""
        system_id = next(candidate for candidate, distance in sorted(distances.items()) if distance == jumps)
        return resolve_chain(simulation.store, UNIVERSE, system_id, HOME.id, radius=5)

    assert HOME.id in chain_at(0).members
    for jumps in range(1, 6):
        chain = chain_at(jumps)
        assert HOME.id in chain.members, jumps
        assert (chain.entrance_id, chain.jumps) == (jita.id, jumps)
        assert any(row.virtual and row.current and row.jumps_from == jumps for row in chain.rows)
    assert chain_at(6) is None


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


# ---------------------------------------------------------------- overlay focus
class Layout:
    """A chain built from edges, with the character somewhere in it."""

    def __init__(self) -> None:
        """Start from home; ``take`` hands out distinct w-space systems."""
        self.simulation = MapSimulation()
        self.pool = [system for system in UNIVERSE.all_systems()
                     if system.system_class in ("C1", "C2", "C3", "C4", "C5", "C6") and system.id != HOME.id]

    def attach(self, parent: int) -> int:
        """Connect a new w-space system below ``parent`` by wormhole."""
        system_id = self.pool.pop(0).id
        self.simulation.tracker.add_wormhole(parent, system_id)
        return system_id

    def line(self, parent: int, length: int) -> list[int]:
        """Add a line of ``length`` systems below ``parent``."""
        found = []
        for _ in range(length):
            parent = self.attach(parent)
            found.append(parent)
        return found

    def connect(self, system_id: int, other_id: int) -> None:
        """Add a hole between two systems."""
        self.simulation.tracker.add_wormhole(system_id, other_id)

    def rows(self, where: int, **options):
        """Return the full rows and the focused rows with the character at ``where``."""
        chain = resolve_chain(self.simulation.store, UNIVERSE, where, HOME.id, max_depth=1000)
        options.setdefault("home_id", HOME.id)
        return chain.rows, focus_rows(chain.rows, self.simulation.store, UNIVERSE, **options)


def ids(rows) -> list[int]:
    """The system IDs of the rows that are real systems."""
    return [row.system_id for row in rows if not row.kind]


def test_focus_folds_a_30_system_chain() -> None:
    """Character five deep: the radius-2 neighbourhood, the way home folded, one summary per other branch."""
    layout = Layout()
    path = layout.line(HOME.id, 5)                    # HOME - p1 .. p5 (character)
    child = layout.attach(path[4])                    # below the character
    sibling = layout.attach(path[3])                  # beside the character
    far = layout.attach(sibling)                      # three jumps away
    branch_a = layout.line(HOME.id, 8)                # a branch off home
    layout.line(path[0], 6)                           # a branch off p1, inside the folded way home
    side = layout.line(path[2], 5)                    # off p3: 3 jumps from the character
    full, rows = layout.rows(path[4])
    assert len(full) == 1 + 5 + 1 + 1 + 1 + 8 + 6 + 5 == 28
    path_row = next(row for row in rows if row.kind == "path")
    assert [row.kind for row in rows].count("path") == 1 and path_row.summary_count == 2
    assert path_row.leads_home is False               # home itself is the first row
    shown = ids(rows)
    assert shown[0] == HOME.id and set(shown) == {HOME.id, path[2], path[3], path[4], child, sibling}
    branches = {row.system_id: row.summary_count for row in rows if row.kind == "branch"}
    assert branches == {far: 1, branch_a[0]: 8, side[0]: 5}
    assert rows[0].prefix == "" and all(row.prefix.endswith(("├ ", "└ ")) for row in rows[1:])
    assert [row.system_id for row in rows if row.current] == [path[4]]


def test_focus_radius_options() -> None:
    """Radius 1 shows less than 2, and 0 shows the whole chain."""
    layout = Layout()
    path = layout.line(HOME.id, 3)
    layout.line(path[0], 3)
    full, one = layout.rows(path[1], radius=1)
    _, two = layout.rows(path[1], radius=2)
    _, whole = layout.rows(path[1], radius=0)
    assert len(one) == 5 and len(two) == 6 and ids(whole) == ids(full)
    assert {row.system_id for row in whole if row.system_id} >= set(path)


def test_focus_goes_deeper_along_the_character_branch() -> None:
    """Below the character the chain shows beyond the radius; a side branch stays folded at the radius."""
    layout = Layout()
    path = layout.line(HOME.id, 2)
    way_on = layout.line(path[1], 6)                  # the character's branch, 6 deep
    side = layout.line(path[0], 6)                    # another branch off p1
    _, rows = layout.rows(path[1], radius=2, max_rows=40)
    shown = ids(rows)
    assert all(system in shown for system in way_on)
    assert side[0] in shown and side[1] not in shown
    assert any(row.kind == "branch" and row.system_id == side[1] and row.summary_count == 5 for row in rows)


def test_focus_way_on_stops_at_the_row_cap() -> None:
    """The way on is cut by the row cap, farthest first."""
    layout = Layout()
    path = layout.line(HOME.id, 1)
    way_on = layout.line(path[0], 20)
    _, rows = layout.rows(path[0], radius=1, max_rows=8)
    assert len(rows) <= 8 and way_on[0] in ids(rows) and way_on[-1] not in ids(rows)
    assert next(row for row in rows if row.kind).summary_count >= 1


@pytest.mark.parametrize("system_class, key", [("HS", "high"), ("LS", "low"), ("NS", "null")])
def test_focus_exit_marker_per_class(system_class: str, key: str) -> None:
    """A hidden branch with a hole to one k-space class carries exactly that marker."""
    layout = Layout()
    path = layout.line(HOME.id, 2)
    hidden = layout.line(HOME.id, 3)
    layout.connect(hidden[2], pick_system(system_class).id)
    _, rows = layout.rows(path[1], radius=1)
    summary = next(row for row in rows if row.kind == "branch")
    assert summary.system_id == hidden[0] and summary.exits == frozenset({key}) and summary.summary_count == 4


def test_focus_two_classes_and_none() -> None:
    """A branch with a nullsec exit two down and a highsec one shows both; a branch without exits shows none."""
    layout = Layout()
    path = layout.line(HOME.id, 2)
    hidden = layout.line(HOME.id, 3)
    layout.connect(hidden[1], pick_system("NS").id)
    layout.connect(hidden[2], pick_system("HS").id)
    plain = layout.line(HOME.id, 2)
    _, rows = layout.rows(path[1], radius=1)
    by_first = {row.system_id: row for row in rows if row.kind == "branch"}
    assert by_first[hidden[0]].exits == frozenset({"null", "high"})
    assert by_first[plain[0]].exits == frozenset()


def test_focus_wspace_holes_are_no_markers() -> None:
    """Holes into other w-space (Thera too) never count as exits, on summary rows or shown rows."""
    layout = Layout()
    path = layout.line(HOME.id, 2)
    hidden = layout.line(HOME.id, 2)
    layout.connect(hidden[1], pick_system("TH").id)
    layout.connect(path[1], pick_system("C6").id)
    _, rows = layout.rows(path[1], radius=1)
    assert all(row.exits == frozenset() for row in rows)


def test_focus_marks_shown_wormhole_rows() -> None:
    """A shown wormhole row with a hole to k-space has the class; the k-space row itself has none."""
    layout = Layout()
    path = layout.line(HOME.id, 1)
    jita = UNIVERSE.by_name("Jita")
    layout.connect(path[0], jita.id)
    _, rows = layout.rows(path[0])
    assert next(row for row in rows if row.system_id == path[0]).exits == frozenset({"high"})
    assert next(row for row in rows if row.system_id == jita.id).exits == frozenset()


def test_focus_whole_chain_respects_the_row_cap() -> None:
    """Radius 'whole chain' with 41 systems never shows more than max_rows rows and keeps the key rows."""
    layout = Layout()
    path = layout.line(HOME.id, 4)
    for start in (HOME.id, path[0], path[1], path[2]):
        layout.line(start, 9)
    layout.line(path[3], 3)
    full, _ = layout.rows(path[3])
    assert len(full) == 44
    for cap in (15, 8, 5):
        _, rows = layout.rows(path[3], radius=0, max_rows=cap)
        assert len(rows) <= cap, cap
        assert rows[0].system_id == HOME.id and any(row.current for row in rows)
    _, rows = layout.rows(path[3], radius=0, max_rows=15)
    assert any(row.kind == "branch" for row in rows)


def test_focus_deep_line_folds_the_way_home() -> None:
    """A long line with radius 0 still fits the cap, by folding the way home."""
    layout = Layout()
    line = layout.line(HOME.id, 30)
    _, rows = layout.rows(line[-1], radius=0, max_rows=10)
    assert len(rows) <= 10 and any(row.kind == "path" for row in rows) and rows[-1].current


def test_focus_shows_a_single_system_instead_of_folding_it() -> None:
    """Folding one system would save nothing, so it is shown."""
    layout = Layout()
    line = layout.line(HOME.id, 4)                    # radius 2 would hide only p1
    _, rows = layout.rows(line[3])
    assert ids(rows) == [HOME.id] + line and not any(row.kind for row in rows)


def test_focus_character_in_k_space_next_to_the_chain() -> None:
    """The virtual row of a character out in k-space is shown, below its entrance."""
    layout = Layout()
    jita = UNIVERSE.by_name("Jita")
    layout.connect(HOME.id, jita.id)
    outside = next(system for system, jumps in sorted(UNIVERSE.gate_distances(jita.id, 2).items()) if jumps == 2)
    _, rows = layout.rows(outside)
    virtual = next(row for row in rows if row.virtual)
    assert virtual.current and virtual.jumps_from == 2 and virtual.entrance_id == jita.id
    assert [row.system_id for row in rows][-2:] == [jita.id, outside]


def test_focus_leads_home_and_validation() -> None:
    """A folded way that contains home carries the flag; bad arguments are refused."""
    layout = Layout()
    line = layout.line(HOME.id, 6)
    chain = resolve_chain(layout.simulation.store, UNIVERSE, line[-1], None, max_depth=1000)
    store = layout.simulation.store
    assert not any(row.leads_home for row in focus_rows(chain.rows, store, UNIVERSE))
    rows = focus_rows(chain.rows, store, UNIVERSE, home_id=line[0])
    assert next(row for row in rows if row.kind == "path").leads_home
    with pytest.raises(ValueError):
        focus_rows(chain.rows, store, UNIVERSE, max_rows=4)
    with pytest.raises(ValueError):
        focus_rows(chain.rows, store, UNIVERSE, radius=-1)
    assert focus_rows([], store, UNIVERSE) == []

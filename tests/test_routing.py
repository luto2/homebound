"""Routes over gates, map wormholes and EVE-Scout links, on the real universe data."""
import pytest

from conftest import HOME, UNIVERSE
from homebound import routing
from homebound.mapdb.store import LIFETIME_1H, LIFETIME_EXPIRED, Connection
from homebound.routing import RouteOptions, ScoutLink

JITA, AMARR, PERIMETER = (UNIVERSE.by_name(name).id for name in ("Jita", "Amarr", "Perimeter"))
TAMA, THERA = UNIVERSE.by_name("Tama").id, UNIVERSE.by_name("Thera").id


def route(origin: int, destination: int, connections: list[Connection] = (), links: list[ScoutLink] = (),
          **options) -> routing.Route | None:
    """Return the one route from origin to destination."""
    return routing.routes(UNIVERSE, origin, [destination], connections, links, RouteOptions(**options))[0]


def hole(**values) -> Connection:
    """A connection between home and Jita."""
    return Connection(1, min(HOME.id, JITA), max(HOME.id, JITA), **values)


def test_gate_route_and_preferences() -> None:
    """Shortest goes through low-sec; safer takes longer and spends fewer jumps outside high-sec."""
    def outside_high_sec(found: routing.Route) -> int:
        """Count the systems entered that the autopilot doesn't count as high-sec."""
        return sum(1 for hop in found.hops[1:] if UNIVERSE.by_id(hop.system_id).security < routing.HIGH_SEC)

    shortest, safer = route(TAMA, AMARR), route(TAMA, AMARR, preference="safer")
    assert shortest.hops[0] == routing.Hop(TAMA, "") and shortest.hops[-1].system_id == AMARR
    assert all(hop.via == routing.VIA_GATE for hop in shortest.hops[1:])
    assert safer.jumps > shortest.jumps and outside_high_sec(safer) < outside_high_sec(shortest)
    less_secure = route(JITA, AMARR, preference="less_secure")
    assert less_secure.jumps > route(JITA, AMARR).jumps
    assert route(JITA, JITA).jumps == 0 and route(JITA, PERIMETER).jumps == 1


def test_map_wormhole_shortcut_and_filters() -> None:
    """A mapped hole is used unless expired, EOL, critical or too small for the ship, as the options say."""
    assert route(HOME.id, JITA) is None
    found = route(HOME.id, AMARR, [hole(wormhole_type="H296")])
    assert found.hops[1] == routing.Hop(JITA, routing.VIA_WORMHOLE)
    assert found.jumps == 1 + route(JITA, AMARR).jumps
    assert route(HOME.id, JITA, [hole(lifetime=LIFETIME_EXPIRED)], use_eol=True) is None
    assert route(HOME.id, JITA, [hole(lifetime=LIFETIME_1H)], use_eol=False) is None
    assert route(HOME.id, JITA, [hole(lifetime=LIFETIME_1H)]).jumps == 1
    assert route(HOME.id, JITA, [hole(mass="critical")]) is None
    assert route(HOME.id, JITA, [hole(mass="critical")], use_mass_critical=True).jumps == 1
    assert route(HOME.id, JITA, [hole(frigate_only=1)]) is None
    assert route(HOME.id, JITA, [hole(frigate_only=1)], ship_size="S").jumps == 1
    assert route(HOME.id, JITA, [hole(wormhole_type="K162", size="M")], ship_size="L") is None
    assert route(HOME.id, JITA, [hole(wormhole_type="K162")], ship_size="XL").jumps == 1   # unknown size fits


def test_eve_scout_links_reach_wormhole_space() -> None:
    """Thera links lead into w-space when EVE-Scout is on and the ship fits."""
    links = [ScoutLink(THERA, PERIMETER, "thera", size="L"), ScoutLink(THERA, HOME.id, "thera", size="L")]
    found = route(JITA, HOME.id, links=links)
    assert [hop.system_id for hop in found.hops] == [JITA, PERIMETER, THERA, HOME.id]
    assert found.hops[2].via == routing.VIA_THERA and found.hops[2].link is links[0]
    assert route(JITA, HOME.id, links=links, use_eve_scout=False) is None
    assert route(JITA, HOME.id, links=links, ship_size="XL") is None
    turnur = UNIVERSE.by_name("Turnur").id
    assert route(turnur, HOME.id, links=[ScoutLink(turnur, HOME.id, "turnur")]).hops[1].via == routing.VIA_TURNUR


def test_options_are_checked() -> None:
    """Unknown preferences, sizes and non-bool switches are refused."""
    with pytest.raises(ValueError):
        RouteOptions(preference="fastest")
    with pytest.raises(ValueError):
        RouteOptions(ship_size="XXL")
    with pytest.raises(TypeError):
        RouteOptions(use_eol=1)
    with pytest.raises(TypeError):
        routing.connection_usable(UNIVERSE, "hole", RouteOptions())
    with pytest.raises(ValueError):
        routing.search(UNIVERSE, {}, JITA, "fastest")
    assert routing.path_to({}, JITA) is None and routing.Route(JITA).jumps == 0

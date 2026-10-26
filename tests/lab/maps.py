"""Synthetic homes for the Lab tests: rooms on a 10 cm walkable grid, built in code."""
from vesper.lab.worldmap import Grid, Room, WorldMap

CELL = 0.1


def grid_from(width_m, height_m, walkable, origin=(0.0, 0.0)):
    """A grid whose cell (i, j) is walkable when `walkable(x, z)` holds at its centre."""
    w, h = round(width_m / CELL), round(height_m / CELL)
    g = Grid(origin=origin, cell=CELL, width=w, height=h, walkable=bytearray(w * h))
    for j in range(h):
        for i in range(w):
            x, z = g.center(i, j)
            g.walkable[j * w + i] = 1 if walkable(x, z) else 0
    return g


def rect(x0, z0, x1, z1):
    return ((x0, z0), (x1, z0), (x1, z1), (x0, z1))


def two_rooms(gap=(1.5, 2.5)):
    """Kitchen [0,4]x[0,4] and living room [4,8]x[0,4]; a 20 cm wall at x=4 with an opening at `gap` (z)."""
    def walkable(x, z):
        in_wall = 3.9 <= x < 4.1
        return not in_wall or (gap is not None and gap[0] <= z < gap[1])
    return WorldMap(scene="test/two-rooms", rooms=[Room("kitchen", "kitchen", rect(0, 0, 4, 4)),
                                                   Room("living", "living room", rect(4, 0, 8, 4))],
                    grid=grid_from(8, 4, walkable))


def house():
    """Hall [0,3]x[0,6], kitchen [3,7]x[0,3], bedroom [3,7]x[3,6], porch outside [-2,0]x[2,4].

    Walls are 20 cm thick and lie outside every room polygon, as in a navmesh raster: doorways are gaps
    hall-kitchen at z in [1,2], hall-bedroom at z in [4,5], and the front door hall-porch at z in [2.5,3.5].
    The kitchen and the bedroom share a solid wall.
    """
    rooms = [Room("hall", "hallway", rect(0.1, 0, 2.9, 6)), Room("kitchen", "kitchen", rect(3.1, 0, 7, 2.9)),
             Room("bedroom", "bedroom", rect(3.1, 3.1, 7, 6)),
             Room("porch", "porch/terrace/deck/driveway", rect(-2, 2, -0.1, 4), outdoor=True)]

    def walkable(x, z):
        if -0.1 <= x < 0.1:  # the front wall
            return 2.5 <= z < 3.5
        if 2.9 <= x < 3.1:  # hall | kitchen, bedroom
            return 1 <= z < 2 or 4 <= z < 5
        if x >= 3.1 and 2.9 <= z < 3.1:  # kitchen | bedroom: solid
            return False
        if x < -0.1:
            return 2 <= z < 4  # only the porch is outside
        return 0 <= z < 6 and x < 7
    return WorldMap(scene="test/house", rooms=rooms, grid=grid_from(9, 6, walkable, origin=(-2.0, 0.0)))


def grid_house(cols=6, rows=4, size=6.0):
    """cols x rows rooms of `size` metres (r<col><row>), 20 cm walls, and a 1 m doorway in the middle of every
    wall between neighbours: a home-sized floor (36 x 24 m by default) where a walk crosses many rooms."""
    def walkable(x, z):
        in_vwall = any(abs(x - c * size) < 0.1 for c in range(1, cols))
        in_hwall = any(abs(z - r * size) < 0.1 for r in range(1, rows))
        if in_vwall and in_hwall:
            return False
        if in_vwall:
            return abs(z % size - size / 2) < 0.5
        if in_hwall:
            return abs(x % size - size / 2) < 0.5
        return True
    rooms = [Room(f"r{c}{r}", "room", rect(c * size + (0.1 if c else 0), r * size + (0.1 if r else 0),
                                          (c + 1) * size - (0.1 if c < cols - 1 else 0),
                                          (r + 1) * size - (0.1 if r < rows - 1 else 0)))
             for c in range(cols) for r in range(rows)]
    return WorldMap(scene="test/grid-house", rooms=rooms, grid=grid_from(cols * size, rows * size, walkable))

"""A stand-in for Blender's `bpy`, as much of it as `vesper lab dress`'s build script uses: enough to run the script
here and see what it would build in Blender (plan W9)."""
from types import SimpleNamespace


class _Named(dict):
    """A Blender ID: a name and custom properties (obj["category"])."""

    def __init__(self, name):
        super().__init__()
        self.name = name

    __eq__ = object.__eq__  # an ID is itself, whatever its properties
    __hash__ = object.__hash__


class Mesh(_Named):
    def __init__(self, name):
        super().__init__(name)
        self.vertices, self.faces, self.materials = [], [], []

    def from_pydata(self, vertices, edges, faces):
        self.vertices, self.faces = [tuple(v) for v in vertices], [tuple(f) for f in faces]

    def update(self):
        pass


class Object(_Named):
    def __init__(self, name, data):
        super().__init__(name)
        self.data, self.location = data, (0.0, 0.0, 0.0)
        self.empty_display_type, self.empty_display_size = "PLAIN_AXES", 1.0
        self.users_collection = []


class Links(list):
    def __init__(self, owner, attr):
        super().__init__()
        self.owner, self.attr = owner, attr

    def link(self, item):
        self.append(item)
        if self.attr == "objects":
            item.users_collection.append(self.owner)

    def unlink(self, item):
        self.remove(item)


class Collection(_Named):
    def __init__(self, name):
        super().__init__(name)
        self.objects, self.children = Links(self, "objects"), Links(self, "children")

    @property
    def all_objects(self):
        return list(self.objects) + [o for c in self.children for o in c.all_objects]


class Store(dict):
    def __init__(self, make):
        super().__init__()
        self.make = make

    def new(self, name, *args):
        item = self.make(name, *args)
        while item.name in self:  # Blender's own way: the next free name
            item.name += ".001"
        self[item.name] = item
        return item

    def remove(self, item, do_unlink=True):
        self.pop(item.name, None)
        for c in getattr(item, "users_collection", []):
            if item in c.objects:
                c.objects.unlink(item)

    def __iter__(self):
        return iter(list(self.values()))


def Material(name):
    m = _Named(name)
    m.diffuse_color = (0.8, 0.8, 0.8, 1.0)
    return m


def make():
    """A fresh `bpy` with an empty scene."""
    scene = Collection("Scene Collection")
    exports = []
    layer = SimpleNamespace(layer_collection=SimpleNamespace(children={}), active_layer_collection=None)

    class Children(Links):
        def link(self, item):
            super().link(item)
            layer.layer_collection.children[item.name] = SimpleNamespace(collection=item)

        def unlink(self, item):
            super().unlink(item)
            layer.layer_collection.children.pop(item.name, None)

    scene.children = Children(scene, "children")
    data = SimpleNamespace(meshes=Store(Mesh), objects=Store(Object), collections=Store(Collection),
                           materials=Store(Material))
    context = SimpleNamespace(scene=SimpleNamespace(collection=scene), view_layer=layer)

    def gltf(**kwargs):
        exports.append(kwargs)
        return {"FINISHED"}
    ops = SimpleNamespace(export_scene=SimpleNamespace(gltf=gltf))
    return SimpleNamespace(data=data, context=context, ops=ops, exports=exports)

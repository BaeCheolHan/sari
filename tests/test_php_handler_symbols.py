from sari.core.parsers.handlers.php import PHPHandler


class _Node:
    def __init__(self, node_type, children=None, text=""):
        self.type = node_type
        self.children = children or []
        self._text = text


def _get_t(node):
    return node._text


def test_php_handler_recognizes_trait_and_interface_symbols():
    handler = PHPHandler()

    trait_node = _Node(
        "trait_declaration",
        children=[_Node("name", text="MyTrait")],
    )
    interface_node = _Node(
        "interface_declaration",
        children=[_Node("name", text="MyContract")],
    )

    trait_res = handler.handle_node(trait_node, _get_t, lambda *_: None, ".php", {})
    iface_res = handler.handle_node(interface_node, _get_t, lambda *_: None, ".php", {})

    assert trait_res[0] == "class"
    assert trait_res[1] == "MyTrait"
    assert trait_res[3] is True

    assert iface_res[0] == "class"
    assert iface_res[1] == "MyContract"
    assert iface_res[3] is True

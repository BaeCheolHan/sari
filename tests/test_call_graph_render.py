from sari.core.services.call_graph.render import render_tree


def test_render_tree_tolerates_non_list_children():
    tree = {
        "name": "root",
        "path": "/tmp/root.py",
        "children": (
            {"name": "child", "path": "/tmp/child.py", "confidence": 1.0, "children": ()},
        ),
    }

    out = render_tree(tree, max_print_depth=2)

    assert "root (root.py)" in out
    assert "child (child.py)" in out

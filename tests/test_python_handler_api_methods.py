from sari.core.parsers.handlers.python import PythonHandler


class _Node:
    def __init__(self, node_type, children=None, text="", parent=None, start_point=(0, 0)):
        self.type = node_type
        self.children = children or []
        self._text = text
        self.parent = parent
        self.start_point = start_point
        for c in self.children:
            c.parent = self


def _get_t(node):
    return node._text


def test_python_handler_extract_api_info_supports_put_patch_delete():
    handler = PythonHandler()

    decorated = _Node(
        "decorated_definition",
        children=[
            _Node("decorator", text="@app.put('/u')"),
            _Node("decorator", text="@app.patch('/u')"),
            _Node("decorator", text="@app.delete('/u')"),
        ],
    )

    res = handler.extract_api_info(decorated, _get_t, lambda *_: None)

    assert res["http_path"] == "/u"
    assert set(res["http_methods"]) == {"PUT", "PATCH", "DELETE"}


def test_python_handler_extract_api_info_handles_nested_decorator_args():
    handler = PythonHandler()

    decorated = _Node(
        "decorated_definition",
        children=[
            _Node("decorator", text="@router.get(build_path('/users/{id}'))"),
        ],
    )

    res = handler.extract_api_info(decorated, _get_t, lambda *_: None)

    assert res["http_path"] == "/users/{id}"
    assert res["http_methods"] == ["GET"]


def test_python_handler_handles_async_function_definition():
    handler = PythonHandler()
    node = _Node("async_function_definition", text="async def run(): pass")
    kind, name, _meta, is_valid = handler.handle_node(
        node,
        _get_t,
        lambda n: "run",
        ".py",
        {},
    )
    assert is_valid is True
    assert kind == "function"
    assert name == "run"


def test_python_handler_inheritance_preserves_qualified_base_name():
    handler = PythonHandler()
    class_node = _Node(
        "class_definition",
        children=[
            _Node(
                "argument_list",
                children=[
                    _Node("attribute", text="framework.base.Model"),
                ],
            )
        ],
        start_point=(9, 0),
    )

    rels = handler.handle_relation(
        class_node,
        {
            "get_t": _get_t,
            "parent_name": "UserModel",
            "parent_sid": "sid-user-model",
        },
    )

    assert len(rels) == 1
    assert rels[0].rel_type == "extends"
    assert rels[0].to_name == "framework.base.Model"

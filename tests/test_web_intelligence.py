import pytest
import json
from sari.core.parsers.ast_engine import ASTEngine

def test_react_functional_component_truth():
    """
    Verify that React functional components are recognized as major symbols.
    """
    engine = ASTEngine()
    code = (
        "const UserProfile = ({ name }) => {\n"
        "    return <div>{name}</div>;\n"
        "};\n"
    )
    symbols, _ = engine.extract_symbols("User.jsx", "javascript", code)
    assert any(s[1] == "UserProfile" and s[2] == "class" for s in symbols)
    print(f"\nDEBUG: React SUCCESS. Found: {[s[1] for s in symbols]}")

def test_express_route_extraction_truth():
    """
    Verify that Express route handlers are extracted with their paths.
    """
    engine = ASTEngine()
    code = (
        "app.get('/api/users', (req, res) => {\n"
        "    res.send('ok');\n"
        "});\n"
    )
    symbols, _ = engine.extract_symbols("server.js", "javascript", code)
    route = next(s for s in symbols if "route.get" in s[1])
    assert route[2] == "method"
    metadata = json.loads(route[9])
    assert "/api/users" in metadata["route_path"]
    print(f"DEBUG: Express SUCCESS. Route: {route[1]} Path: {metadata['route_path']}")

def test_vue_script_block_truth():
    """
    Verify that Vue .vue files have their script blocks parsed.
    """
    engine = ASTEngine()
    code = (
        "<template>\n"
        "  <h1>{{ msg }}</h1>\n"
        "</template>\n"
        "<script>\n"
        "export default {\n"
        "  data() { return { msg: 'Hello Vue' } }\n"
        "}\n"
        "</script>\n"
    )
    symbols, _ = engine.extract_symbols("App.vue", "vue", code)
    assert any("data" in s[1] for s in symbols)
    print(f"DEBUG: Vue SUCCESS. Found symbols in script: {[s[1] for s in symbols]}")
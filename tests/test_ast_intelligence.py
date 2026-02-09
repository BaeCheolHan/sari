import pytest
import json
from sari.core.parsers.ast_engine import ASTEngine

def test_java_annotation_extraction():
    """
    Verify that the modernized AST engine extracts Spring annotations.
    Standard Tuple: 0:sid, 1:path, 2:root, 3:name, 4:kind, 5:line, 6:end, 7:content, 8:parent, 9:meta, 10:doc, 11:qual
    """
    engine = ASTEngine()
    code = (
        "@RestController\n"
        "@RequestMapping(\"/api\")\n"
        "public class MyController {\n"
        "    @GetMapping(\"/hello\")\n"
        "    public String sayHello() {\n"
        "        return \"world\";\n"
        "    }\n"
    "}\n"
    )
    symbols, _ = engine.extract_symbols("MyController.java", "java", code)
    
    # Priority Fix: Use attributes
    cls_symbol = next(s for s in symbols if s.name == "MyController")
    metadata = cls_symbol.meta
    
    assert "RestController" in metadata["annotations"]
    assert "RequestMapping" in metadata["annotations"]
    
    func_symbol = next(s for s in symbols if s.name == "sayHello")
    func_meta = func_symbol.meta
    
    assert "GetMapping" in func_meta["annotations"]

def test_python_decorator_extraction():
    """
    Verify that decorators in Python are also correctly captured.
    """
    engine = ASTEngine()
    code = (
        "@app.route(\"/\")\n"
        "@login_required\n"
        "def index():\n"
        "    pass\n"
    )
    symbols, _ = engine.extract_symbols("app.py", "python", code)
    
    # Priority Fix: Use attributes
    idx_symbol = next(s for s in symbols if s.name == "index")
    metadata = idx_symbol.meta
    
    assert "login_required" in metadata["annotations"]
    assert any("route" in a for a in metadata["annotations"])
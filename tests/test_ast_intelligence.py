import pytest
import json
from sari.core.parsers.ast_engine import ASTEngine

def test_java_annotation_extraction():
    """
    Verify that the modernized AST engine extracts Spring annotations.
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
    # --- FIX: UNPACK PROPERLY ---
    symbols, _ = engine.extract_symbols("MyController.java", "java", code)
    
    # 2. Verify Class Annotation
    cls_symbol = next(s for s in symbols if s[1] == "MyController")
    metadata = json.loads(cls_symbol[7])
    
    assert "RestController" in metadata["annotations"]
    assert "RequestMapping" in metadata["annotations"]
    
    # 3. Verify Method Annotation
    func_symbol = next(s for s in symbols if s[1] == "sayHello")
    func_meta = json.loads(func_symbol[7])
    
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
    # --- FIX: UNPACK PROPERLY ---
    symbols, _ = engine.extract_symbols("app.py", "python", code)
    
    idx_symbol = next(s for s in symbols if s[1] == "index")
    metadata = json.loads(idx_symbol[7])
    
    assert "login_required" in metadata["annotations"]
    assert "route" in metadata["annotations"]

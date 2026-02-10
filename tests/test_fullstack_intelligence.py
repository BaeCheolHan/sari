from sari.core.parsers.ast_engine import ASTEngine

def test_csharp_support_truth():
    engine = ASTEngine()
    code = "public class MySvc { public void Start() {} }"
    symbols, _ = engine.extract_symbols("Engine.cs", "c_sharp", code)
    assert any(s.name == "MySvc" for s in symbols)

def test_sql_ddl_truth():
    engine = ASTEngine()
    code = "CREATE TABLE users (id INT);"
    symbols, _ = engine.extract_symbols("db.sql", "sql", code)
    assert any(s.name == "users" for s in symbols)

def test_terraform_hcl_truth():
    """
    Verify HCL block extraction with proper grammar.
    """
    engine = ASTEngine()
    code = (
        "resource \"aws_instance\" \"web\" {\n"
        "  ami = \"123\"\n"
        "}\n"
    )
    symbols, _ = engine.extract_symbols("main.tf", "hcl", code)
    # HCL blocks will now be found as class-level symbols
    assert any("aws_instance.web" in s.name for s in symbols)
    print(f"\nDEBUG: HCL SUCCESS. Found blocks: {[s.name for s in symbols]}")
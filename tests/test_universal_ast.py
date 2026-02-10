from sari.core.parsers.ast_engine import ASTEngine

def test_investigate_swift_ast_nodes():
    """
    INVESTIGATION: Print all node types in a Swift file.
    """
    engine = ASTEngine()
    lang_obj = engine._get_language("swift")
    assert lang_obj is not None, "Swift parser must be loaded"
        
    from tree_sitter import Parser
    parser = Parser(lang_obj)
    
    code = "@objc class MainViewController { func myFun() {} }"
    tree = parser.parse(code.encode())
    
    print("\nDEBUG: Swift AST Node types found:")
    def walk(node):
        print(f"DEBUG: Node type: {node.type}")
        for child in node.children:
            walk(child)
    walk(tree.root_node)

def test_rust_support_truth():
    engine = ASTEngine()
    code = "struct SariTurbo { speed: u64 }\nfn main() {}"
    symbols, _ = engine.extract_symbols("main.rs", "rust", code)
    assert any(s.name == "SariTurbo" for s in symbols)
    print(f"\nDEBUG: Rust SUCCESS. Found: {[s.name for s in symbols]}")
import pytest
import os
import json
from sari.core.parsers.ast_engine import ASTEngine

def test_investigate_kotlin_ast_nodes():
    """
    INVESTIGATION: Print all node types in a Kotlin file to find the real names.
    """
    engine = ASTEngine()
    lang_obj = engine._get_language("kotlin")
    from tree_sitter import Parser
    parser = Parser()
    parser.language = lang_obj
    
    code = "@RestController class MyClass { fun myFun() {} }"
    tree = parser.parse(code.encode())
    
    print("\nDEBUG: Kotlin AST Node types found:")
    def walk(node):
        print(f"DEBUG: Node type: {node.type}")
        for child in node.children:
            walk(child)
            
    walk(tree.root_node)

def test_kotlin_support_verification():
    # Existing test logic...
    pass
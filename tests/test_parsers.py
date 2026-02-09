import pytest
import json
from sari.core.parsers.base import BaseParser
from sari.core.parsers.common import _qualname, _symbol_id, _safe_compile
from sari.core.parsers.python import PythonParser

def test_base_parser_sanitize():
    parser = BaseParser()
    assert parser.sanitize('print("hello") // comment') == 'print("")'
    # BaseParser only handles //, not # (which is language specific)
    assert parser.sanitize('x = "single" // comment') == 'x = ""'

def test_base_parser_clean_doc():
    parser = BaseParser()
    lines = [
        "/**",
        " * Hello",
        " * World",
        " */"
    ]
    assert parser.clean_doc(lines) == "Hello\nWorld"

def test_common_utils():
    assert _qualname("Parent", "Child") == "Parent.Child"
    assert _qualname("", "Top") == "Top"
    sid = _symbol_id("path", "kind", "qual")
    assert len(sid) == 40

def test_python_parser():
    parser = PythonParser()
    content = '''
class MyClass:
    """Class doc"""
    @my_decorator
    def my_method(self):
        print("hi")
        other_func()

def top_func():
    pass
'''
    symbols, relations = parser.extract("test.py", content)
    
    assert len(symbols) == 3
    s_class = next(s for s in symbols if s.kind == "class")
    assert s_class.name == "MyClass"
    assert s_class.doc == "Class doc"
    
    # Method
    s_method = next(s for s in symbols if s.kind == "method")
    assert s_method.name == "my_method"
    meta = s_method.meta
    assert "@my_decorator" in meta["decorators"]
    
    # Relations: check if other_func is called
    assert any(rel.to_name == "other_func" for rel in relations)
    assert any(rel.to_name == "print" for rel in relations)

    def test_python_parser_fallback():

        parser = PythonParser()

        content = "class MyClass: def invalid( syntax"

        symbols, relations = parser.extract("test.py", content)

        # Standard Format

        assert any(s.name == "MyClass" for s in symbols)

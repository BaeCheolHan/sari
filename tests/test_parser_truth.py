from sari.core.parsers.factory import ParserFactory

def test_python_parser_pure_logic():
    """
    Verify if the Python parser actually works in the current environment.
    """
    code = "class MyTruth:\n    def verify(self):\n        return True\n"
    parser = ParserFactory.get_parser(".py")
    
    assert parser is not None, "ParserFactory must return a Python parser"
    
    symbols, _ = parser.extract("test.py", code)
    
    print(f"\nDEBUG: Extracted symbols: {symbols}")
    
    names = [s.name for s in symbols]
    assert "MyTruth" in names
    assert "verify" in names
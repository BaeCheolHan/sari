import pytest
from sari.core.parsers.ast_engine import ASTEngine

def test_javascript_react_component_extraction():
    """React 스타일의 변수 할당형 컴포넌트 추출 검증"""
    code = """
    import React from 'react';
    
    const MyComponent = ({ prop1 }) => {
        return <div>{prop1}</div>;
    };
    
    function StandardFunc() {
        return null;
    }
    
    export default MyComponent;
    """
    
    engine = ASTEngine()
    
    # symbols_list의 각 항목은 ParserSymbol 객체임
    symbols, _ = engine.extract_symbols("test.js", "javascript", code)
    
    names = [s.name for s in symbols]
    print(f"\n[DEBUG TEST] Extracted Symbol Names: {names}")
    
    assert "MyComponent" in names
    assert "StandardFunc" in names

def test_javascript_lexical_variable_extraction():
    """일반적인 const/let 변수 선언 내의 함수 추출 검증"""
    code = """
    const helper = function(a) { return a + 1; };
    let api = (data) => data.json();
    """
    engine = ASTEngine()
    symbols, _ = engine.extract_symbols("test.js", "javascript", code)
    
    names = [s.name for s in symbols]
    print(f"[DEBUG TEST] Extracted Names: {names}")
    assert "helper" in names
    assert "api" in names

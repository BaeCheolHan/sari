import pytest
import json
from sari.core.parsers.ast_engine import ASTEngine

def test_mybatis_xml_symbol_extraction():
    """
    Verify that MyBatis SQL IDs are extracted as searchable symbols.
    """
    engine = ASTEngine()
    code = (
        "<mapper namespace=\"com.example.UserMapper\">\n"
        "    <select id=\"findByUsername\" resultType=\"User\">\n"
        "        SELECT * FROM users WHERE username = #{username}\n"
        "    </select>\n"
        "    <update id=\"updateLastLogin\">\n"
        "        UPDATE users SET last_login = NOW() WHERE id = #{id}\n"
        "    </update>\n"
        "</mapper>\n"
    )
    symbols, _ = engine.extract_symbols("UserMapper.xml", "xml", code)
    
    assert len(symbols) == 2
    select_sql = next(s for s in symbols if s[1] == "findByUsername")
    assert select_sql[2] == "method"
    meta = json.loads(select_sql[7])
    assert meta["mybatis_op"] == "select"
    print(f"\nDEBUG: MyBatis SUCCESS. Found SQL ID: {select_sql[1]}")

def test_querydsl_generated_class_detection():
    """
    Verify that QueryDSL Q-classes are identified as generated.
    """
    engine = ASTEngine()
    code = (
        "public class QUser extends EntityPathBase<User> {\n"
        "    public final StringPath username = createString(\"username\");\n"
        "}\n"
    )
    symbols, _ = engine.extract_symbols("QUser.java", "java", code)
    
    q_cls = next(s for s in symbols if s[1] == "QUser")
    meta = json.loads(q_cls[7])
    assert meta["generated"] is True
    assert meta["framework"] == "QueryDSL"
    print(f"DEBUG: QueryDSL SUCCESS. Identified generated class: {q_cls[1]}")

def test_jsp_basic_understanding():
    """
    Verify that JSP files yield some logical markers.
    """
    engine = ASTEngine()
    code = (
        "<html>\n"
        "<body>\n"
        "    <% String name = request.getParameter(\"name\"); %>\n"
        "    <h1>Hello <%= name %></h1>\n"
        "</body>\n"
        "</html>\n"
    )
    symbols, _ = engine.extract_symbols("hello.jsp", "jsp", code)
    assert any("scriptlet" in s[1] for s in symbols)
    print(f"DEBUG: JSP SUCCESS. Found scriptlet in file.")
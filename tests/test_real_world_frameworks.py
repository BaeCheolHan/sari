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
    select_sql = next(s for s in symbols if s.name == "findByUsername")
    assert select_sql.kind == "method"
    
    meta = select_sql.meta
    assert meta["mybatis_tag"] == "select"

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
    
    q_cls = next(s for s in symbols if s.name == "QUser")
    meta = q_cls.meta
    assert meta["generated"] is True

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
    # Generic extraction for JSP (fallback to text or simple mock tree)
    symbols, _ = engine.extract_symbols("hello.jsp", "jsp", code)
    # Non-AST language fallback contract: must return a collection, even if empty.
    assert isinstance(symbols, list)

from sari.mcp.tools.call_graph import build_call_graph

def test_algorithm_entropy_suppression_with_fixture(db, workspace):
    """엔트로피 억제 알고리즘 최종 검증 (임계치와 점수 밸런싱)"""
    conn = db.get_read_connection()
    db_rid = conn.execute("SELECT root_id FROM roots LIMIT 1").fetchone()[0]
    
    conn.execute("PRAGMA foreign_keys = OFF")
    m_path = f"{db_rid}/main.py"
    l_path = f"{db_rid}/log.py"
    
    # 1. 심볼 데이터 (main, log)
    conn.execute("INSERT INTO symbols (symbol_id, path, root_id, name, kind, line, end_line, content, qualname) VALUES (?,?,?,?,?,?,?,?,?)",
                 ("sid-main", m_path, db_rid, "main", "function", 1, 5, "", "main"))
    conn.execute("INSERT INTO symbols (symbol_id, path, root_id, name, kind, line, end_line, content, qualname) VALUES (?,?,?,?,?,?,?,?,?)",
                 ("sid-log", l_path, db_rid, "log", "function", 1, 5, "", "log"))
    
    # 2. 메인 호출 관계
    conn.execute("""
        INSERT INTO symbol_relations (from_path, from_root_id, from_symbol, from_symbol_id, to_path, to_root_id, to_symbol, to_symbol_id, rel_type, line) 
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (m_path, db_rid, "main", "sid-main", l_path, db_rid, "log", "sid-log", "calls", 1))
    
    # 3. 통계용 노이즈 (60개) - 이로 인해 'log'는 엔트로피 페널티를 받아야 함
    for i in range(60):
        conn.execute("""
            INSERT INTO symbol_relations (from_path, from_root_id, from_symbol, from_symbol_id, to_path, to_root_id, to_symbol, to_symbol_id, rel_type, line)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (f"{db_rid}/f_{i}.py", db_rid, f"f_{i}", f"sid-f-{i}", l_path, db_rid, "log", "sid-log", "calls", 1))
    
    conn.execute("PRAGMA foreign_keys = ON")

    # 4. 조회 (ID와 경로 모두 명시하여 정확한 추적 보장)
    graph = build_call_graph({"symbol_id": "sid-main", "name": "main", "depth": 1, "path": m_path}, db, [db_rid])
    
    # 5. 검증
    children = graph["downstream"]["children"]
    log_node = next((c for c in children if c["name"] == "log"), None)
    
    # 이제 임계치(0.05)보다 높으므로 발견되어야 함
    assert log_node is not None, f"Log node should be present at low confidence! Graph: {graph}"
    
    # 점수가 극단적으로 낮음을 확인 (페널티 적용 증명)
    # 기본 0.5 + 근접보너스 0.15 - 페널티 0.8 = -0.15 -> 최소값 0.1
    assert log_node["confidence"] <= 0.2
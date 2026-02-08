
import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.absolute()))

import time
import concurrent.futures
from sari.core.db.main import LocalSearchDB
from sari.core.db.models import Symbol

def worker_write(db_path, worker_id, count=100):
    """
    Simulates an indexer worker writing to the DB with retry logic.
    """
    db = LocalSearchDB(db_path)
    conn = db.db.connection()
    
    # 1. Insert Root with retry
    root_id = f"root_{worker_id}"
    max_retries = 5
    for attempt in range(max_retries):
        try:
            with db.db.atomic():
                # Root table: root_id, root_path, real_path, ...
                conn.execute(
                    "INSERT OR IGNORE INTO roots (root_id, root_path, real_path, created_ts, updated_ts) VALUES (?, ?, ?, ?, ?)",
                    (root_id, f"/tmp/worker_{worker_id}", f"/tmp/worker_{worker_id}", int(time.time()), int(time.time()))
                )
            break  # Success
        except Exception as e:
            if "database is locked" in str(e) and attempt < max_retries - 1:
                time.sleep(0.1 * (2 ** attempt))  # Exponential backoff
                continue
            print(f"Worker {worker_id} failed to insert root: {e}")
            return False

    # Write symbols with retry
    try:
        for i in range(count):
            path = f"/tmp/file_{worker_id}_{i}.py"
            
            # 2. Insert File with retry
            for attempt in range(max_retries):
                try:
                    with db.db.atomic():
                        # Files table: path, rel_path, root_id, repo, mtime, size, content
                        conn.execute(
                            "INSERT OR REPLACE INTO files (path, rel_path, root_id, repo, mtime, size, content) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (path, f"file_{worker_id}_{i}.py", root_id, "repo", int(time.time()), 100, b"content")
                        )
                    break  # Success
                except Exception as e:
                    if "database is locked" in str(e) and attempt < max_retries - 1:
                        time.sleep(0.05 * (2 ** attempt))  # Exponential backoff
                        continue
                    raise

            rows = []
             # Parser Tuple: (path, name, kind, line, end_line, content, parent, metadata, docstring, qualname, symbol_id)
            row = (
                path,
                f"symbol_{worker_id}_{i}",
                "function",
                10, 20, "def foo(): pass",
                "root", "{}", "doc", f"foo_{worker_id}_{i}", f"sym_{worker_id}_{i}"
            )
            rows.append(row)
            db.upsert_symbols_tx(None, rows)
            time.sleep(0.01) # Simulate processing time
            
        return True
    except Exception as e:
        print(f"Worker {worker_id} failed: {e}")
        return False
    finally:
        db.close()

def test_concurrent_db_writes(tmp_path):
    """
    Test that multiple processes/threads can write to the same DB without locking error.
    """
    db_file = tmp_path / "index.db"
    db_path = str(db_file)
    
    # Init DB
    main_db = LocalSearchDB(db_path)
    main_db.close()
    
    workers = 4
    items_per_worker = 50
    
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as executor:
        futures = []
        for i in range(workers):
            futures.append(executor.submit(worker_write, db_path, i, items_per_worker))
            
        results = [f.result() for f in futures]
        
    assert all(results), f"Some workers failed: {results}"
    
    # Verify data
    check_db = LocalSearchDB(db_path)
    count = Symbol.select().count()
    check_db.close()
    
    assert count == workers * items_per_worker

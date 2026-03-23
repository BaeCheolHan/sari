
import os
import pathlib
import sys
import time
import json
from unittest.mock import patch

# Add src to sys.path to import solidlsp
sys.path.append(str(pathlib.Path(__file__).parent.parent.parent / "src"))

from solidlsp.language_servers.pyrefly_server import PyreflyServer
from solidlsp.ls_config import LanguageServerConfig, Language
from solidlsp.settings import SolidLSPSettings

def test_deep_debug_pyrefly():
    repo_root = str(pathlib.Path(__file__).parent.parent.parent.absolute())
    print(f"--- DEEP DEBUG: Pyrefly at {repo_root} ---")
    
    os.environ["SARI_PYTHON_LSP_PROVIDER"] = "pyrefly"
    os.environ["SARI_PYREFLY_ANALYSIS_MODE"] = "full"
    
    # Enable tracing in config
    config = LanguageServerConfig(
        code_language=Language.PYTHON,
        trace_lsp_communication=True  # This should trigger logging in our protocol handler
    )
    settings = SolidLSPSettings()
    
    server = PyreflyServer(config, repo_root, settings)
    
    try:
        print("Starting Pyrefly...")
        server.start()
        
        # 1. didOpen target file
        target_file = "src/sari/http/meta_endpoints.py"
        target_abs_path = os.path.join(repo_root, target_file)
        with open(target_abs_path, "r") as f:
            content = f.read()
        
        print(f"Sending didOpen for {target_file}...")
        server.server.notify.did_open_text_document({
            "textDocument": {
                "uri": pathlib.Path(target_abs_path).as_uri(),
                "languageId": "python",
                "version": 1,
                "text": content
            }
        })

        # 2. didOpen a known reference file (to see if it helps)
        ref_file = "src/sari/http/app.py"
        ref_abs_path = os.path.join(repo_root, ref_file)
        if os.path.exists(ref_abs_path):
            with open(ref_abs_path, "r") as f:
                ref_content = f.read()
            print(f"Sending didOpen for {ref_file}...")
            server.server.notify.did_open_text_document({
                "textDocument": {
                    "uri": pathlib.Path(ref_abs_path).as_uri(),
                    "languageId": "python",
                    "version": 1,
                    "text": ref_content
                }
            })

        print("Waiting for indexing (5s)...")
        # Watch for any "progress" notifications if possible (manual check of output)
        time.sleep(5)
        
        # 3. Find coordinates
        line, col = -1, -1
        lines = content.splitlines()
        for i, text in enumerate(lines):
            if "def status_endpoint" in text:
                line = i
                col = text.find("status_endpoint")
                break
        
        if line == -1:
            print("Symbol not found!")
            return

        print(f"Requesting references at {line}:{col}...")
        refs = server.request_references(target_file, line, col)
        
        print(f"\n--- DEBUG RESULTS ---")
        print(f"Count: {len(refs)}")
        if len(refs) == 0:
            print("STILL 0. Check the captured LSP trace above for cancellation or empty responses.")
        else:
            for i, ref in enumerate(refs):
                print(f"[{i+1}] {ref.get('uri')}")
        print("----------------------")

    except Exception as e:
        print(f"Error during debug: {e}")
    finally:
        print("Stopping server...")
        server.stop()

if __name__ == "__main__":
    test_deep_debug_pyrefly()

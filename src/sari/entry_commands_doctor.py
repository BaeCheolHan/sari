import json
import sys


def _cmd_doctor() -> int:
    try:
        from sari.mcp.tools.doctor import execute_doctor
        from urllib.parse import unquote

        res = execute_doctor({})

        if isinstance(res, dict) and "content" in res:
            content = res["content"][0]["text"]
            try:
                data = json.loads(content)
                print(json.dumps(data, ensure_ascii=False, indent=2))
                return 0
            except Exception:
                pass

            if content.startswith("PACK1"):
                lines = content.splitlines()
                for line in lines:
                    if line.startswith("t:"):
                        encoded_val = line[2:]
                        decoded_val = unquote(encoded_val)
                        try:
                            data = json.loads(decoded_val)
                            print(json.dumps(data, ensure_ascii=False, indent=2))
                            return 0
                        except Exception:
                            print(decoded_val)
                            return 0

            print(content)
        return 0
    except Exception as e:
        print(f"Doctor failed: {e}", file=sys.stderr)
        return 1


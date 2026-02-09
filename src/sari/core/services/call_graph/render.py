
from typing import Dict, Any

def render_tree(tree: Dict[str, Any], max_print_depth: int = 2) -> str:
    """
    호출 그래프 트리를 ASCII 형식의 텍스트로 렌더링합니다.
    디버깅 및 사용자에게 직관적인 시각화를 제공합니다.
    
    Args:
        tree: 중첩된 딕셔너리 형태의 트리 구조
        max_print_depth: 보여줄 최대 깊이
        
    Returns:
        ASCII 트리 문자열
    """
    lines = []
    
    def _visit(node: Dict, prefix: str = "", is_last: bool = True, depth: int = 0):
        if depth > max_print_depth:
            lines.append(f"{prefix}...")
            return

        name = node.get("name") or "?"
        path = node.get("path") or ""
        
        # 파일 경로의 마지막 부분(basename)만 표시하여 간결하게 표현
        short_path = path.split("/")[-1] if path else ""
        
        rel = node.get("rel_type")
        rel_tag = f"[{rel}] " if rel else ""
        
        # 트리 구조 표현을 위한 가지(branch) 문자 선택 (├── 또는 └──)
        connector = "└── " if is_last else "├── "
        display = f"{prefix}{connector}{rel_tag}{name} ({short_path})" if depth > 0 else f"{name} ({short_path})"
        lines.append(display)

        children = node.get("children", [])
        # 중요도(confidence) 순으로 정렬하여 상위 결과 우선 표시
        children.sort(key=lambda x: x.get("confidence", 0), reverse=True)
        
        count = len(children)
        for i, child in enumerate(children):
            # 다음 뎁스를 위한 들여쓰기 접두어 계산
            new_prefix = prefix + ("    " if is_last else "│   ")
            _visit(child, new_prefix, i == count - 1, depth + 1)

    _visit(tree)
    return "\n".join(lines)

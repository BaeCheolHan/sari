
import time
from typing import Optional

class GraphBudget:
    """
    호출 그래프 생성 시 리소스 사용을 제한하는 예산(Budget) 관리 클래스입니다.
    노드 수, 엣지 수, 최대 깊이, 실행 시간 등을 추적하고 제한을 초과하면 탐색을 중단시킵니다.
    """
    
    def __init__(self, max_nodes: int, max_edges: int, max_depth: int, max_time_ms: int):
        self.nodes = 0
        self.edges = 0
        self.max_nodes = max_nodes
        self.max_edges = max_edges
        self.max_depth = max_depth
        self.ts_start = time.time()
        self.max_time = max_time_ms / 1000.0
        self.truncated = False
        self.truncate_reason: Optional[str] = None

    def check_time(self) -> bool:
        """실행 시간 제한을 초과했는지 확인합니다. 초과 시 truncate 플래그를 설정하고 False를 반환합니다."""
        if (time.time() - self.ts_start) > self.max_time:
            self.truncated = True
            self.truncate_reason = "time_limit"
            return False
        return True

    def can_add_node(self) -> bool:
        """노드를 추가할 수 있는지(예산 내인지) 확인합니다."""
        if self.nodes >= self.max_nodes:
            self.truncated = True
            self.truncate_reason = "node_limit"
            return False
        return True

    def can_add_edge(self) -> bool:
        """엣지를 추가할 수 있는지(예산 내인지) 확인합니다."""
        if self.edges >= self.max_edges:
            self.truncated = True
            self.truncate_reason = "edge_limit" 
            return False
        return True

    def bump_node(self):
        """방문한 노드 수를 1 증가시킵니다."""
        self.nodes += 1

    def bump_edge(self):
        """방문한 엣지 수를 1 증가시킵니다."""
        self.edges += 1

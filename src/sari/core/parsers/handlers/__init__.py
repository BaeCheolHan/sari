from .java import JavaHandler
from .python import PythonHandler
from .javascript import JavaScriptHandler
from .go import GoHandler
from .rust import RustHandler
from .bash import BashHandler

class HandlerRegistry:
    def __init__(self):
        java = JavaHandler()
        python = PythonHandler()
        js = JavaScriptHandler()
        go = GoHandler()
        rust = RustHandler()
        bash = BashHandler()
        
        self.handlers = {
            "java": java, "kt": java, "kts": java,
            "py": python, "python": python,
            "js": js, "jsx": js, "ts": js, "tsx": js, "javascript": js, "typescript": js,
            "go": go,
            "rs": rust, "rust": rust,
            "sh": bash, "bash": bash
        }

    def get_handler(self, ext: str):
        return self.handlers.get(ext)

from sari.core.parsers.base import BaseHandler
from .java import JavaHandler
from .python import PythonHandler
from .javascript import JavaScriptHandler
from .go import GoHandler
from .rust import RustHandler
from .bash import BashHandler
from .sql import SQLHandler
from .hcl import HCLHandler
from .vue import VueHandler
from .kotlin import KotlinHandler
from .php import PHPHandler
from .ruby import RubyHandler
from .yaml import YAMLHandler
from .xml import XmlHandler

class HandlerRegistry:
    def __init__(self):
        java = JavaHandler()
        python = PythonHandler()
        js = JavaScriptHandler()
        go = GoHandler()
        rust = RustHandler()
        bash = BashHandler()
        sql = SQLHandler()
        hcl = HCLHandler()
        vue = VueHandler()
        kotlin = KotlinHandler()
        php = PHPHandler()
        ruby = RubyHandler()
        yaml = YAMLHandler()
        xml = XmlHandler()
        
        self.handlers = {
            "java": java,
            "kt": kotlin, "kts": kotlin, "kotlin": kotlin,
            "py": python, "python": python,
            "js": js, "jsx": js, "ts": js, "tsx": js, "javascript": js, "typescript": js,
            "go": go,
            "rs": rust, "rust": rust,
            "sh": bash, "bash": bash,
            "sql": sql,
            "hcl": hcl, "tf": hcl,
            "vue": vue,
            "php": php,
            "rb": ruby, "ruby": ruby,
            "yaml": yaml, "yml": yaml,
            "xml": xml
        }

    def get_handler(self, ext: str):
        return self.handlers.get(ext)

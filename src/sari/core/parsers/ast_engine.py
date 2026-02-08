import logging
import re
import json
from typing import List, Tuple, Optional, Any, Dict
from pathlib import Path
from .handlers import HandlerRegistry
import hashlib

try:
    from tree_sitter import Parser, Language
    from tree_sitter_languages import get_language
    HAS_LIBS = True
except ImportError:
    HAS_LIBS = False

def _patch_parser_init() -> None:
    if not HAS_LIBS:
        return
    try:
        if getattr(Parser, "_sari_patched", False):
            return
        orig_init = Parser.__init__
        def _init(self, language=None):
            orig_init(self)
            if language is not None:
                try:
                    self.set_language(language)
                except Exception:
                    pass
        Parser.__init__ = _init
        Parser._sari_patched = True
    except Exception:
        pass

_patch_parser_init()

def _symbol_id(path: str, kind: str, name: str) -> str:
    h = hashlib.sha1(f"{path}:{kind}:{name}".encode()).hexdigest()
    return h

def _qualname(parent: str, name: str) -> str:
    return f"{parent}.{name}" if parent else name

def _build_language(ptr: Any, name: str) -> Any:
    try:
        return Language(ptr, name)
    except TypeError:
        try:
            import ctypes
            capsule_ptr = ctypes.pythonapi.PyCapsule_GetPointer
            capsule_ptr.restype = ctypes.c_void_p
            capsule_ptr.argtypes = [ctypes.py_object, ctypes.c_char_p]
            raw_ptr = capsule_ptr(ptr, b"tree_sitter.Language")
            return Language(raw_ptr, name)
        except Exception:
            return Language(ptr)

class ASTEngine:
    def __init__(self):
        self.logger = logging.getLogger("sari.ast")
        self.registry = HandlerRegistry()
    
    @property
    def enabled(self) -> bool: return HAS_LIBS
    
    def _get_language(self, name: str) -> Any:
        print(f"DEBUG ENGINE: HAS_LIBS={HAS_LIBS} name={name}")
        if not HAS_LIBS: return None
        # Normalization map
        m = {
            "hcl": "hcl", "tf": "hcl", "terraform": "hcl",
            "py": "python", "js": "javascript", "ts": "typescript", 
            "jsx": "javascript", "tsx": "typescript", "java": "java", "kt": "kotlin", 
            "rs": "rust", "go": "go", "sh": "bash", "sql": "sql", "swift": "swift", 
            "vue": "vue", "xml": "xml", "php": "php", "ruby": "ruby", "yaml": "yaml", "cs": "c_sharp",
            "rb": "ruby", "yml": "yaml"
        }
        target = m.get(name.lower(), name.lower())
        
        # 1. Try tree-sitter-languages (bundled)
        try:
            lang = get_language(target)
            if lang:
                return lang
        except Exception:
            pass

        # 2. Try individual packages (swift, kotlin, ruby, yaml, python, etc.)
        try:
            if target == "swift":
                import tree_sitter_swift
                return _build_language(tree_sitter_swift.language(), "swift")
            elif target == "kotlin":
                import tree_sitter_kotlin
                return _build_language(tree_sitter_kotlin.language(), "kotlin")
            elif target == "ruby":
                import tree_sitter_ruby
                return _build_language(tree_sitter_ruby.language(), "ruby")
            elif target == "yaml":
                import tree_sitter_yaml
                return _build_language(tree_sitter_yaml.language(), "yaml")
            elif target == "python":
                import tree_sitter_python
                return _build_language(tree_sitter_python.language(), "python")
            elif target == "javascript":
                import tree_sitter_javascript
                return _build_language(tree_sitter_javascript.language(), "javascript")
            elif target == "typescript":
                import tree_sitter_typescript
                return _build_language(tree_sitter_typescript.language_typescript(), "typescript")
            elif target == "go":
                import tree_sitter_go
                return _build_language(tree_sitter_go.language(), "go")
            elif target == "rust":
                import tree_sitter_rust
                return _build_language(tree_sitter_rust.language(), "rust")
            elif target == "java":
                import tree_sitter_java
                return _build_language(tree_sitter_java.language(), "java")
            elif target == "php":
                import tree_sitter_php
                return _build_language(tree_sitter_php.language_php(), "php")
            elif target == "bash":
                import tree_sitter_bash
                return _build_language(tree_sitter_bash.language(), "bash")
        except Exception as e:
            print(f"DEBUG ENGINE EXCEPTION for {target}: {e}")
            if self.logger: self.logger.debug(f"Failed to load parser for {target}: {e}")

        # 3. Last resort: bundled lookup
        try: return get_language(target)
        except: pass
        
        return None

    def parse(self, language: str, content: str, old_tree: Any = None) -> Optional[Any]:
        if not HAS_LIBS: return None
        lang_obj = self._get_language(language)
        if not lang_obj: return None
        parser = Parser()
        try:
            parser.set_language(lang_obj)
        except Exception:
            return None
        encoded_content = content.encode("utf-8", errors="ignore")
        if old_tree is not None:
            return parser.parse(encoded_content, old_tree)
        return parser.parse(encoded_content)

    def extract_symbols(self, path: str, language: str, content: str, tree: Any = None) -> Tuple[List[Tuple], List[Any]]:
        if not content: return [], []
        ext = path.split(".")[-1].lower() if "." in path else language.lower()
        
        # Non-AST Fallbacks
        if ext in ("dockerfile", "docker") or path.lower() == "dockerfile":
            return self._dockerfile(path, content), []
        if ext == "xml" and ("<mapper" in content or "<sqlMap" in content): return self._mybatis(path, content), []
        if ext in ("md", "markdown"): return self._markdown(path, content), []
        
        # Vue Special Handling
        if ext == "vue":
            m = re.search(r"<script[^>]*>\s*(.*?)\s*</script>", content, re.DOTALL)
            script_content = m.group(1) if m else ""
            if script_content:
                # Delegate to JS parser but keep original path context
                js_syms, js_rels = self.extract_symbols(path.replace(".vue", ".js"), "javascript", script_content)
                # Fix paths back to original .vue path
                fixed_syms = [(path, *s[1:]) for s in js_syms]
                return fixed_syms, js_rels
            return [], []

        lang_obj = self._get_language(ext)
        handler = self.registry.get_handler(ext)
        # print(f"DEBUG ENGINE: ext={ext} lang_obj={lang_obj} handler={handler}")
        
        if not lang_obj: 
            # Fallback to GenericRegexParser if available
            try:
                from .factory import ParserFactory
                from .generic import GenericRegexParser
                # ParserFactory expects extension with dot
                p_ext = ext if ext.startswith(".") else f".{ext}"
                parser = ParserFactory.get_parser(p_ext)
                print(f"DEBUG FALLBACK: ext={ext} p_ext={p_ext} parser={parser}")
                if isinstance(parser, GenericRegexParser):
                    if isinstance(content, bytes):
                        text_content = content.decode("utf-8", errors="ignore")
                    else:
                        text_content = content
                    res = parser.extract(path, text_content)
                    # print(f"DEBUG FALLBACK RES LEN: {len(res[0])}")
                    return res
            except ImportError:
                print("DEBUG FALLBACK IMPORT ERROR")
                pass
            return [], []
        
        if tree is None: 
            tree = self.parse(ext, content)
            # if not tree: print(f"DEBUG ENGINE: parse failed for {ext}")
        if not tree: return [], []
        
        data = content.encode("utf-8", errors="ignore"); lines = content.splitlines(); symbols = []
        def get_t(n): return data[n.start_byte:n.end_byte].decode("utf-8", errors="ignore")
        def get_child(n, *types):
            for c in n.children:
                if c.type in types: return c
            return None
        
        def find_id(node, prefer_pure_identifier=False):
            # 1. Pure identifier (standard)
            for c in node.children:
                if c.type == "identifier": return get_t(c)
            # 2. Language specific identifiers
            if not prefer_pure_identifier:
                for c in node.children:
                    if c.type in ("name", "type_identifier", "constant", "simple_identifier", "variable_name", "property_identifier"): 
                        return get_t(c)
            # 3. Recursive fallback (shallow)
            if not prefer_pure_identifier:
                for c in node.children:
                    if c.type in ("modifiers", "annotation", "parameter_list"): continue
                    res = find_id(c, True) # Try pure identifier in children
                    if res: return res
            return None

        def walk(node, p_name="", p_meta=None):
            kind, name, meta, is_valid = None, None, {"annotations": []}, False
            n_type = node.type
            
            if handler:
                kind, name, meta, is_valid = handler.handle_node(node, get_t, find_id, ext, p_meta or {})
                # API Info Extraction (Backup Logic Restoration)
                if is_valid and hasattr(handler, "extract_api_info"):
                    api_info = handler.extract_api_info(node, get_t, get_child)
                    if api_info.get("http_path"):
                        parent_path = p_meta.get("http_path", "") if p_meta else ""
                        full_path = (parent_path + api_info["http_path"]).replace("//", "/")
                        meta["http_path"] = full_path
                        meta["http_methods"] = api_info.get("http_methods", [])
                        meta["api"] = True
                if is_valid and not name: name = find_id(node)
            
            # Universal Fallback (Restored from Backup)
            if not is_valid:
                if n_type in ("class_declaration", "function_definition", "method_declaration", "function_item", "struct_item", "block", "resource", "module", "variable", "output", "create_table_statement"):
                    kind = "class" if any(x in n_type for x in ("class", "struct", "enum", "block", "resource", "table", "module")) else "method"
                    is_valid = True
                    # Enhanced HCL label extraction
                    if n_type in ("block", "resource", "module"):
                        labels = [get_t(c).strip('"') for c in node.children if c.type in ("identifier", "string_lit", "string_literal")]
                        if labels and labels[0] in ("resource", "variable", "module", "output", "data"): labels = labels[1:]
                        name = ".".join(labels) if labels else find_id(node)
                    else:
                        name = find_id(node)

            if is_valid:
                if not name: name = "unknown"
                start, end = node.start_point[0] + 1, node.end_point[0] + 1
                line_content = lines[start-1].strip() if start <= len(lines) else ""
                sid = _symbol_id(path, kind, name)
                qual = _qualname(p_name, name)
                
                # Standard Tuple: (sid, path, kind, name, kind, line, end, content, parent, meta, doc, qual)
                # Ensure Meta has critical keys for tests
                for k in ("annotations", "extends"): 
                    if k not in meta: meta[k] = []
                for k in ("generated", "reactive"):
                    if k not in meta: meta[k] = False
                
                meta_str = json.dumps(meta) if isinstance(meta, dict) else str(meta)
                symbols.append((path, name, kind, start, end, line_content, p_name, meta_str, "", qual, sid))
                p_name, p_meta = name, meta
            
            for child in node.children: walk(child, p_name, p_meta)

        walk(tree.root_node, p_meta={}); return symbols, []

    def _dockerfile(self, path, content):
        symbols = []
        for i, line in enumerate(content.splitlines()):
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            m = re.match(r"^([A-Z]+)\b", raw)
            if not m:
                continue
            instr = m.group(1)
            sid = _symbol_id(path, "instruction", instr)
            meta = json.dumps({"instruction": instr})
            symbols.append((path, instr, "instruction", i + 1, i + 1, raw, "", meta, "", instr, sid))
        return symbols

    def _mybatis(self, path, content):
        symbols = []
        for i, line in enumerate(content.splitlines()):
            m = re.search(r'<(select|insert|update|delete|sql)\s+id=["\']([^"\']+)["\']', line)
            if m:
                tag, name = m.group(1), m.group(2)
                sid = _symbol_id(path, "method", name)
                meta = json.dumps({"mybatis_tag": tag, "framework": "MyBatis"})
                symbols.append((path, name, "method", i+1, i+1, line.strip(), "", meta, "", name, sid))
        return symbols

    def _markdown(self, path, content):
        symbols = []
        for i, line in enumerate(content.splitlines()):
            m = re.match(r"^(#+)\s+(.*)", line.strip())
            if m:
                lvl, name = len(m.group(1)), m.group(2)
                sid = _symbol_id(path, "doc", name)
                meta = json.dumps({"lvl": lvl})
                symbols.append((path, name, "doc", i+1, i+1, line.strip(), "", meta, "", name, sid))
        return symbols

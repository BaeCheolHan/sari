import logging
import re
import json
from typing import List, Tuple, Optional, Any, Dict
from pathlib import Path
from .handlers import HandlerRegistry
from .special_parsers import SpecialParser
import hashlib

try:
    from tree_sitter import Parser, Language
    HAS_LIBS = True
except ImportError:
    HAS_LIBS = False

def _symbol_id(path: str, kind: str, name: str) -> str:
    h = hashlib.sha1(f"{path}:{kind}:{name}".encode()).hexdigest()
    return h

def _qualname(parent: str, name: str) -> str:
    return f"{parent}.{name}" if parent else name

class ASTEngine:
    def __init__(self):
        self.logger = logging.getLogger("sari.ast")
        self.registry = HandlerRegistry()
    
    @property
    def enabled(self) -> bool: return HAS_LIBS
    
    def _get_language(self, name: str) -> Any:
        if self.logger and self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug("AST engine lookup (HAS_LIBS=%s, name=%s)", HAS_LIBS, name)
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
        
        # Try individual packages (modern tree-sitter ^0.23.0 style)
        try:
            if target == "swift":
                import tree_sitter_swift
                return Language(tree_sitter_swift.language())
            elif target == "kotlin":
                import tree_sitter_kotlin
                return Language(tree_sitter_kotlin.language())
            elif target == "ruby":
                import tree_sitter_ruby
                return Language(tree_sitter_ruby.language())
            elif target == "yaml":
                import tree_sitter_yaml
                return Language(tree_sitter_yaml.language())
            elif target == "python":
                import tree_sitter_python
                return Language(tree_sitter_python.language())
            elif target == "javascript":
                import tree_sitter_javascript
                return Language(tree_sitter_javascript.language())
            elif target == "typescript":
                import tree_sitter_typescript
                return Language(tree_sitter_typescript.language_typescript())
            elif target == "go":
                import tree_sitter_go
                return Language(tree_sitter_go.language())
            elif target == "rust":
                import tree_sitter_rust
                return Language(tree_sitter_rust.language())
            elif target == "java":
                import tree_sitter_java
                return Language(tree_sitter_java.language())
            elif target == "php":
                import tree_sitter_php
                return Language(tree_sitter_php.language_php())
            elif target == "bash":
                import tree_sitter_bash
                return Language(tree_sitter_bash.language())
        except Exception as e:
            if self.logger:
                self.logger.debug("Failed to load parser for %s: %s", target, e)
        
        return None

    def parse(self, language: str, content: str, old_tree: Any = None) -> Optional[Any]:
        if not HAS_LIBS: return None
        lang_obj = self._get_language(language)
        if not lang_obj: return None
        try:
            parser = Parser(lang_obj)
        except Exception:
            return None
        encoded_content = content.encode("utf-8", errors="ignore")
        if old_tree is not None:
            return parser.parse(encoded_content, old_tree)
        return parser.parse(encoded_content)

    def extract_symbols(self, path: str, language: str, content: str, tree: Any = None) -> Tuple[List[Tuple], List[Any]]:
        if not content: return [], []
        ext = path.split(".")[-1].lower() if "." in path else language.lower()
        
        # Non-AST Fallbacks using SpecialParser
        if ext in ("dockerfile", "docker") or path.lower() == "dockerfile":
            return SpecialParser.parse_dockerfile(path, content), []
        if ext == "xml" and ("<mapper" in content or "<sqlMap" in content): 
            return SpecialParser.parse_mybatis(path, content), []
        if ext in ("md", "markdown"): 
            return SpecialParser.parse_markdown(path, content), []
        
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
                if self.logger and self.logger.isEnabledFor(logging.DEBUG):
                    self.logger.debug("AST fallback: ext=%s p_ext=%s parser=%s", ext, p_ext, parser)
                if isinstance(parser, GenericRegexParser):
                    if isinstance(content, bytes):
                        text_content = content.decode("utf-8", errors="ignore")
                    else:
                        text_content = content
                    res = parser.extract(path, text_content)
                    # print(f"DEBUG FALLBACK RES LEN: {len(res[0])}")
                    return res
            except ImportError:
                if self.logger and self.logger.isEnabledFor(logging.DEBUG):
                    self.logger.debug("AST fallback import error")
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

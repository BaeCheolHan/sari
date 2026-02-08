import tree_sitter
from tree_sitter import Parser
from tree_sitter_languages import get_language
import sys

def print_tree(node, indent=0):
    txt = node.text.decode('utf-8', errors='ignore').strip().replace('\n', ' ')[:40]
    print("  " * indent + f"{node.type} [{node.start_byte}-{node.end_byte}]: {txt}")
    for child in node.children:
        print_tree(child, indent + 1)

def debug_lang(lang_name, code):
    print(f"\n--- Debugging {lang_name} ---")
    try:
        lang = get_language(lang_name)
        parser = Parser()
        parser = Parser(lang)
        tree = parser.parse(code.encode("utf-8"))
        print_tree(tree.root_node)
    except Exception as e:
        print(f"Error loading {lang_name}: {e}")

# 1. React / JavaScript
debug_lang("javascript", "const UserProfile = ({ name }) => { return <div>{name}</div>; };")

# 2. Express
debug_lang("javascript", "app.get('/api/users', (req, res) => { res.send('ok'); });")

# 3. Bash
debug_lang("bash", "SARI_PORT=47800\nfunction start_daemon() { echo 'hi'; }")

# 4. Terraform (HCL)
debug_lang("terraform", 'resource "aws_instance" "web" { ami = "123" }')
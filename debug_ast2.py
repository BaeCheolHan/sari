import tree_sitter
from tree_sitter import Parser
from tree_sitter_languages import get_language
import sys

def print_tree(node, indent=0):
    txt = node.text.decode('utf-8', errors='ignore').strip().replace('\n', ' ')[:60]
    print("  " * indent + f"{node.type} [{node.start_byte}-{node.end_byte}]: {txt}")
    for child in node.children:
        print_tree(child, indent + 1)

def debug_lang(lang_name, code):
    print(f"\n--- Debugging {lang_name} ---")
    try:
        lang = get_language(lang_name)
        parser = Parser()
        parser.set_language(lang)
        tree = parser.parse(code.encode("utf-8"))
        print_tree(tree.root_node)
    except Exception as e:
        print(f"Error loading {lang_name}: {e}")

# 1. Java Spring Entity
debug_lang("java", """@Entity
@Table(name = "users")
public class User {
    @Id private Long id;
}

public interface UserRepository extends JpaRepository<User, Long> {}
""")

# 2. SQL DDL
debug_lang("sql", "CREATE TABLE users (id INT);")

# 3. HCL (try different name)
debug_lang("hcl", 'resource "aws_instance" "web" { ami = "123" }')

# 4. Bash
debug_lang("bash", "function hello() { echo 'world'; }")

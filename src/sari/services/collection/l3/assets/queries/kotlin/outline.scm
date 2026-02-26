(class_declaration (identifier) @name) @symbol.class
(class_parameter (identifier) @name) @symbol.field
(source_file (function_declaration name: (identifier) @name) @symbol.function)
(class_body (function_declaration name: (identifier) @name) @symbol.method)
(source_file (property_declaration (variable_declaration (identifier) @name) @symbol.field))
(class_body (property_declaration (variable_declaration (identifier) @name) @symbol.field))

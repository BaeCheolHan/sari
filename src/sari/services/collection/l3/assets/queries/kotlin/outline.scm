(class_declaration name: (type_identifier) @name) @symbol.class
(object_declaration name: (type_identifier) @name) @symbol.class
(type_alias name: (type_identifier) @name) @symbol.class
(class_declaration (primary_constructor (class_parameter (simple_identifier) @name) @symbol.field))
(source_file (function_declaration name: (simple_identifier) @name) @symbol.function)
(source_file (property_declaration (variable_declaration (simple_identifier) @name)) @symbol.field)
(class_body (function_declaration name: (simple_identifier) @name) @symbol.method)
(class_body (secondary_constructor) @symbol.method)
(class_body (property_declaration (variable_declaration (simple_identifier) @name)) @symbol.field)

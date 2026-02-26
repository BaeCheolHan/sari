(class_declaration name: (identifier) @name) @definition.class
(method_definition name: (property_identifier) @name) @definition.method
(function_declaration name: (identifier) @name) @definition.function
(lexical_declaration (variable_declarator name: (identifier) @name value: [(arrow_function) (function)])) @definition.function
(variable_declaration (variable_declarator name: (identifier) @name value: [(arrow_function) (function)])) @definition.function
(assignment_expression left: (identifier) @definition.function right: [(arrow_function) (function)])
(assignment_expression left: (member_expression property: (property_identifier) @definition.function) right: [(arrow_function) (function)])
(pair key: (property_identifier) @name value: [(arrow_function) (function)]) @definition.function
(call_expression function: (identifier) @definition.function arguments: (arguments [(arrow_function) (function)]))
(call_expression function: (member_expression property: (property_identifier) @definition.function) arguments: (arguments [(arrow_function) (function)]))
(pair key: (property_identifier) @symbol.field)
(pair key: (string) @symbol.field)
(pair key: (computed_property_name) @symbol.field)
(shorthand_property_identifier) @symbol.field
(shorthand_property_identifier_pattern) @symbol.field
(variable_declarator name: (identifier) @name) @symbol.field
(catch_clause parameter: (identifier) @symbol.variable)

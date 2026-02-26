(module (expression_statement (assignment left: (identifier) @name) @symbol.field))

(class_definition
  name: (identifier) @name) @symbol.class

(module
  (function_definition name: (identifier) @name) @symbol.function)

(module
  (decorated_definition
    (function_definition name: (identifier) @name) @symbol.function))

(class_definition
  body: (block
    (function_definition name: (identifier) @name) @symbol.method))

(class_definition
  body: (block
    (decorated_definition
      (function_definition name: (identifier) @name) @symbol.method)))

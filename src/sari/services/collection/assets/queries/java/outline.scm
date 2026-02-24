(class_declaration
  name: (identifier) @name) @definition.class

(method_declaration
  name: (identifier) @name) @definition.method

(method_invocation
  name: (identifier) @name
  arguments: (argument_list) @reference.call)

(interface_declaration
  name: (identifier) @name) @definition.interface

(type_list
  (type_identifier) @name) @reference.implementation

(object_creation_expression
  type: (type_identifier) @name) @reference.class

(superclass (type_identifier) @name) @reference.class

; supplement:sari
(package_declaration (scoped_identifier) @name) @symbol.module
(package_declaration (identifier) @name) @symbol.module
(interface_declaration name: (identifier) @name) @symbol.interface
(annotation_type_declaration name: (identifier) @name) @symbol.interface
(record_declaration name: (identifier) @name) @symbol.class
(enum_declaration name: (identifier) @name) @symbol.enum
(constructor_declaration name: (identifier) @name) @symbol.method
(field_declaration (variable_declarator name: (identifier) @name) @symbol.field)
(enum_constant name: (identifier) @name) @symbol.enum_constant

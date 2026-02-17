from __future__ import annotations

from enum import Enum, IntEnum, IntFlag
from typing import Literal, NotRequired, Union

from typing_extensions import TypedDict


URI = str
DocumentUri = str
Uint = int
RegExp = str


class SemanticTokenTypes(Enum):
    """A set of predefined token types. This set is not fixed
    an clients can specify additional token types via the
    corresponding client capabilities.

    @since 3.16.0
    """

    Namespace = "namespace"
    Type = "type"
    """ Represents a generic type. Acts as a fallback for types which can't be mapped to
    a specific type like class or enum. """
    Class = "class"
    Enum = "enum"
    Interface = "interface"
    Struct = "struct"
    TypeParameter = "typeParameter"
    Parameter = "parameter"
    Variable = "variable"
    Property = "property"
    EnumMember = "enumMember"
    Event = "event"
    Function = "function"
    Method = "method"
    Macro = "macro"
    Keyword = "keyword"
    Modifier = "modifier"
    Comment = "comment"
    String = "string"
    Number = "number"
    Regexp = "regexp"
    Operator = "operator"
    Decorator = "decorator"
    """ @since 3.17.0 """


class SemanticTokenModifiers(Enum):
    """A set of predefined token modifiers. This set is not fixed
    an clients can specify additional token types via the
    corresponding client capabilities.

    @since 3.16.0
    """

    Declaration = "declaration"
    Definition = "definition"
    Readonly = "readonly"
    Static = "static"
    Deprecated = "deprecated"
    Abstract = "abstract"
    Async = "async"
    Modification = "modification"
    Documentation = "documentation"
    DefaultLibrary = "defaultLibrary"


class DocumentDiagnosticReportKind(Enum):
    """The document diagnostic report kinds.

    @since 3.17.0
    """

    Full = "full"
    """ A diagnostic report with a full
    set of problems. """
    Unchanged = "unchanged"
    """ A report indicating that the last
    returned report is still accurate. """


class ErrorCodes(IntEnum):
    """Predefined error codes."""

    ParseError = -32700
    InvalidRequest = -32600
    MethodNotFound = -32601
    InvalidParams = -32602
    InternalError = -32603
    ServerNotInitialized = -32002
    """ Error code indicating that a server received a notification or
    request before the server has received the `initialize` request. """
    UnknownErrorCode = -32001


class LSPErrorCodes(IntEnum):
    RequestFailed = -32803
    """ A request failed but it was syntactically correct, e.g the
    method name was known and the parameters were valid. The error
    message should contain human readable information about why
    the request failed.

    @since 3.17.0 """
    ServerCancelled = -32802
    """ The server cancelled the request. This error code should
    only be used for requests that explicitly support being
    server cancellable.

    @since 3.17.0 """
    ContentModified = -32801
    """ The server detected that the content of a document got
    modified outside normal conditions. A server should
    NOT send this error code if it detects a content change
    in it unprocessed messages. The result even computed
    on an older state might still be useful for the client.

    If a client decides that a result is not of any use anymore
    the client should cancel the request. """
    RequestCancelled = -32800
    """ The client has canceled a request and a server as detected
    the cancel. """


class FoldingRangeKind(Enum):
    """A set of predefined range kinds."""

    Comment = "comment"
    """ Folding range for a comment """
    Imports = "imports"
    """ Folding range for an import or include """
    Region = "region"
    """ Folding range for a region (e.g. `#region`) """


class SymbolKind(IntEnum):
    """A symbol kind."""

    File = 1
    Module = 2
    Namespace = 3
    Package = 4
    """
    Represents a package or simply a directory in the filesystem
    """
    Class = 5
    Method = 6
    Property = 7
    Field = 8
    Constructor = 9
    Enum = 10
    Interface = 11
    Function = 12
    Variable = 13
    Constant = 14
    String = 15
    Number = 16
    Boolean = 17
    Array = 18
    Object = 19
    Key = 20
    Null = 21
    EnumMember = 22
    Struct = 23
    Event = 24
    Operator = 25
    TypeParameter = 26

    @classmethod
    def from_int(cls, value: int) -> "SymbolKind":
        for symbol_kind in cls:
            if symbol_kind.value == value:
                return symbol_kind
        raise ValueError(f"Invalid symbol kind: {value}")


class SymbolTag(IntEnum):
    """Symbol tags are extra annotations that tweak the rendering of a symbol.

    @since 3.16
    """

    Deprecated = 1
    """ Render a symbol as obsolete, usually using a strike-out. """


class UniquenessLevel(Enum):
    """Moniker uniqueness level to define scope of the moniker.

    @since 3.16.0
    """

    Document = "document"
    """ The moniker is only unique inside a document """
    Project = "project"
    """ The moniker is unique inside a project for which a dump got created """
    Group = "group"
    """ The moniker is unique inside the group to which a project belongs """
    Scheme = "scheme"
    """ The moniker is unique inside the moniker scheme. """
    Global = "global"
    """ The moniker is globally unique """


class MonikerKind(Enum):
    """The moniker kind.

    @since 3.16.0
    """

    Import = "import"
    """ The moniker represent a symbol that is imported into a project """
    Export = "export"
    """ The moniker represents a symbol that is exported from a project """
    Local = "local"
    """ The moniker represents a symbol that is local to a project (e.g. a local
    variable of a function, a class not visible outside the project, ...) """


class InlayHintKind(IntEnum):
    """Inlay hint kinds.

    @since 3.17.0
    """

    Type = 1
    """ An inlay hint that for a type annotation. """
    Parameter = 2
    """ An inlay hint that is for a parameter. """


class MessageType(IntEnum):
    """The message type"""

    Error = 1
    """ An error message. """
    Warning = 2
    """ A warning message. """
    Info = 3
    """ An information message. """
    Log = 4
    """ A log message. """


class TextDocumentSyncKind(IntEnum):
    """Defines how the host (editor) should sync
    document changes to the language server.
    """

    None_ = 0
    """ Documents should not be synced at all. """
    Full = 1
    """ Documents are synced by always sending the full content
    of the document. """
    Incremental = 2
    """ Documents are synced by sending the full content on open.
    After that only incremental updates to the document are
    send. """


class TextDocumentSaveReason(IntEnum):
    """Represents reasons why a text document is saved."""

    Manual = 1
    """ Manually triggered, e.g. by the user pressing save, by starting debugging,
    or by an API call. """
    AfterDelay = 2
    """ Automatic after a delay. """
    FocusOut = 3
    """ When the editor lost focus. """


class CompletionItemKind(IntEnum):
    """The kind of a completion entry."""

    Text = 1
    Method = 2
    Function = 3
    Constructor = 4
    Field = 5
    Variable = 6
    Class = 7
    Interface = 8
    Module = 9
    Property = 10
    Unit = 11
    Value = 12
    Enum = 13
    Keyword = 14
    Snippet = 15
    Color = 16
    File = 17
    Reference = 18
    Folder = 19
    EnumMember = 20
    Constant = 21
    Struct = 22
    Event = 23
    Operator = 24
    TypeParameter = 25


class CompletionItemTag(IntEnum):
    """Completion item tags are extra annotations that tweak the rendering of a completion
    item.

    @since 3.15.0
    """

    Deprecated = 1
    """ Render a completion as obsolete, usually using a strike-out. """


class InsertTextFormat(IntEnum):
    """Defines whether the insert text in a completion item should be interpreted as
    plain text or a snippet.
    """

    PlainText = 1
    """ The primary text to be inserted is treated as a plain string. """
    Snippet = 2
    """ The primary text to be inserted is treated as a snippet.

    A snippet can define tab stops and placeholders with `$1`, `$2`
    and `${3:foo}`. `$0` defines the final tab stop, it defaults to
    the end of the snippet. Placeholders with equal identifiers are linked,
    that is typing in one will update others too.

    See also: https://microsoft.github.io/language-server-protocol/specifications/specification-current/#snippet_syntax """


class InsertTextMode(IntEnum):
    """How whitespace and indentation is handled during completion
    item insertion.

    @since 3.16.0
    """

    AsIs = 1
    """ The insertion or replace strings is taken as it is. If the
    value is multi line the lines below the cursor will be
    inserted using the indentation defined in the string value.
    The client will not apply any kind of adjustments to the
    string. """
    AdjustIndentation = 2
    """ The editor adjusts leading whitespace of new lines so that
    they match the indentation up to the cursor of the line for
    which the item is accepted.

    Consider a line like this: <2tabs><cursor><3tabs>foo. Accepting a
    multi line completion item is indented using 2 tabs and all
    following lines inserted will be indented using 2 tabs as well. """


class DocumentHighlightKind(IntEnum):
    """A document highlight kind."""

    Text = 1
    """ A textual occurrence. """
    Read = 2
    """ Read-access of a symbol, like reading a variable. """
    Write = 3
    """ Write-access of a symbol, like writing to a variable. """


class CodeActionKind(Enum):
    """A set of predefined code action kinds"""

    Empty = ""
    """ Empty kind. """
    QuickFix = "quickfix"
    """ Base kind for quickfix actions: 'quickfix' """
    Refactor = "refactor"
    """ Base kind for refactoring actions: 'refactor' """
    RefactorExtract = "refactor.extract"
    """ Base kind for refactoring extraction actions: 'refactor.extract'

    Example extract actions:

    - Extract method
    - Extract function
    - Extract variable
    - Extract interface from class
    - ... """
    RefactorInline = "refactor.inline"
    """ Base kind for refactoring inline actions: 'refactor.inline'

    Example inline actions:

    - Inline function
    - Inline variable
    - Inline constant
    - ... """
    RefactorRewrite = "refactor.rewrite"
    """ Base kind for refactoring rewrite actions: 'refactor.rewrite'

    Example rewrite actions:

    - Convert JavaScript function to class
    - Add or remove parameter
    - Encapsulate field
    - Make method static
    - Move method to base class
    - ... """
    Source = "source"
    """ Base kind for source actions: `source`

    Source code actions apply to the entire file. """
    SourceOrganizeImports = "source.organizeImports"
    """ Base kind for an organize imports source action: `source.organizeImports` """
    SourceFixAll = "source.fixAll"
    """ Base kind for auto-fix source actions: `source.fixAll`.

    Fix all actions automatically fix errors that have a clear fix that do not require user input.
    They should not suppress errors or perform unsafe fixes such as generating new types or classes.

    @since 3.15.0 """


class TraceValues(Enum):
    Off = "off"
    """ Turn tracing off. """
    Messages = "messages"
    """ Trace messages only. """
    Verbose = "verbose"
    """ Verbose message tracing. """


class MarkupKind(Enum):
    """Describes the content type that a client supports in various
    result literals like `Hover`, `ParameterInfo` or `CompletionItem`.

    Please note that `MarkupKinds` must not start with a `$`. This kinds
    are reserved for internal usage.
    """

    PlainText = "plaintext"
    """ Plain text is supported as a content format """
    Markdown = "markdown"
    """ Markdown is supported as a content format """


class PositionEncodingKind(Enum):
    """A set of predefined position encoding kinds.

    @since 3.17.0
    """

    UTF8 = "utf-8"
    """ Character offsets count UTF-8 code units. """
    UTF16 = "utf-16"
    """ Character offsets count UTF-16 code units.

    This is the default and must always be supported
    by servers """
    UTF32 = "utf-32"
    """ Character offsets count UTF-32 code units.

    Implementation note: these are the same as Unicode code points,
    so this `PositionEncodingKind` may also be used for an
    encoding-agnostic representation of character offsets. """


class FileChangeType(IntEnum):
    """The file event type"""

    Created = 1
    """ The file got created. """
    Changed = 2
    """ The file got changed. """
    Deleted = 3
    """ The file got deleted. """


class WatchKind(IntFlag):
    Create = 1
    """ Interested in create events. """
    Change = 2
    """ Interested in change events """
    Delete = 4
    """ Interested in delete events """


class DiagnosticSeverity(IntEnum):
    """The diagnostic's severity."""

    Error = 1
    """ Reports an error. """
    Warning = 2
    """ Reports a warning. """
    Information = 3
    """ Reports an information. """
    Hint = 4
    """ Reports a hint. """


class DiagnosticTag(IntEnum):
    """The diagnostic tags.

    @since 3.15.0
    """

    Unnecessary = 1
    """ Unused or unnecessary code.

    Clients are allowed to render diagnostics with this tag faded out instead of having
    an error squiggle. """
    Deprecated = 2
    """ Deprecated or obsolete code.

    Clients are allowed to rendered diagnostics with this tag strike through. """


class CompletionTriggerKind(IntEnum):
    """How a completion was triggered"""

    Invoked = 1
    """ Completion was triggered by typing an identifier (24x7 code
    complete), manual invocation (e.g Ctrl+Space) or via API. """
    TriggerCharacter = 2
    """ Completion was triggered by a trigger character specified by
    the `triggerCharacters` properties of the `CompletionRegistrationOptions`. """
    TriggerForIncompleteCompletions = 3
    """ Completion was re-triggered as current completion list is incomplete """


class SignatureHelpTriggerKind(IntEnum):
    """How a signature help was triggered.

    @since 3.15.0
    """

    Invoked = 1
    """ Signature help was invoked manually by the user or by a command. """
    TriggerCharacter = 2
    """ Signature help was triggered by a trigger character. """
    ContentChange = 3
    """ Signature help was triggered by the cursor moving or by the document content changing. """


class CodeActionTriggerKind(IntEnum):
    """The reason why code actions were requested.

    @since 3.17.0
    """

    Invoked = 1
    """ Code actions were explicitly requested by the user or by an extension. """
    Automatic = 2
    """ Code actions were requested automatically.

    This typically happens when current selection in a file changes, but can
    also be triggered when file content changes. """


class FileOperationPatternKind(Enum):
    """A pattern kind describing if a glob pattern matches a file a folder or
    both.

    @since 3.16.0
    """

    File = "file"
    """ The pattern matches a file only. """
    Folder = "folder"
    """ The pattern matches a folder only. """


class NotebookCellKind(IntEnum):
    """A notebook cell kind.

    @since 3.17.0
    """

    Markup = 1
    """ A markup-cell is formatted source that is used for display. """
    Code = 2
    """ A code-cell is source code. """


class ResourceOperationKind(Enum):
    Create = "create"
    """ Supports creating new files and folders. """
    Rename = "rename"
    """ Supports renaming existing files and folders. """
    Delete = "delete"
    """ Supports deleting existing files and folders. """


class FailureHandlingKind(Enum):
    Abort = "abort"
    """ Applying the workspace change is simply aborted if one of the changes provided
    fails. All operations executed before the failing operation stay executed. """
    Transactional = "transactional"
    """ All operations are executed transactional. That means they either all
    succeed or no changes at all are applied to the workspace. """
    TextOnlyTransactional = "textOnlyTransactional"
    """ If the workspace edit contains only textual file changes they are executed transactional.
    If resource changes (create, rename or delete file) are part of the change the failure
    handling strategy is abort. """
    Undo = "undo"
    """ The client tries to undo the operations already executed. But there is no
    guarantee that this is succeeding. """


class PrepareSupportDefaultBehavior(IntEnum):
    Identifier = 1
    """ The client's default behavior is to select the identifier
    according the to language's syntax rule. """


class TokenFormat(Enum):
    Relative = "relative"


Definition = Union["Location", list["Location"]]
""" The definition of a symbol represented as one or many {@link Location locations}.
For most programming languages there is only one location at which a symbol is
defined.

Servers should prefer returning `DefinitionLink` over `Definition` if supported
by the client. """

DefinitionLink = "LocationLink"
""" Information about where a symbol is defined.

Provides additional metadata over normal {@link Location location} definitions, including the range of
the defining symbol """

LSPArray = list["LSPAny"]
""" LSP arrays.
@since 3.17.0 """

LSPAny = Union["LSPObject", "LSPArray", str, int, Uint, float, bool, None]
""" The LSP any type.
Please note that strictly speaking a property with the value `undefined`
can't be converted into JSON preserving the property name. However for
convenience it is allowed and assumed that all these properties are
optional as well.
@since 3.17.0 """

Declaration = Union["Location", list["Location"]]
""" The declaration of a symbol representation as one or many {@link Location locations}. """

DeclarationLink = "LocationLink"
""" Information about where a symbol is declared.

Provides additional metadata over normal {@link Location location} declarations, including the range of
the declaring symbol.

Servers should prefer returning `DeclarationLink` over `Declaration` if supported
by the client. """

InlineValue = Union["InlineValueText", "InlineValueVariableLookup", "InlineValueEvaluatableExpression"]
""" Inline value information can be provided by different means:
- directly as a text value (class InlineValueText).
- as a name to use for a variable lookup (class InlineValueVariableLookup)
- as an evaluatable expression (class InlineValueEvaluatableExpression)
The InlineValue types combines all inline value types into one type.

@since 3.17.0 """

DocumentDiagnosticReport = Union["RelatedFullDocumentDiagnosticReport", "RelatedUnchangedDocumentDiagnosticReport"]
""" The result of a document diagnostic pull request. A report can
either be a full report containing all diagnostics for the
requested document or an unchanged report indicating that nothing
has changed in terms of diagnostics in comparison to the last
pull request.

@since 3.17.0 """

PrepareRenameResult = Union["Range", "__PrepareRenameResult_Type_1", "__PrepareRenameResult_Type_2"]

DocumentSelector = list["DocumentFilter"]
""" A document selector is the combination of one or many document filters.

@sample `let sel:DocumentSelector = [{ language: 'typescript' }, { language: 'json', pattern: '**/tsconfig.json' }]`;

The use of a string as a document filter is deprecated @since 3.16.0. """

ProgressToken = Union[int, str]

ChangeAnnotationIdentifier = str
""" An identifier to refer to a change annotation stored with a workspace edit. """

WorkspaceDocumentDiagnosticReport = Union[
    "WorkspaceFullDocumentDiagnosticReport",
    "WorkspaceUnchangedDocumentDiagnosticReport",
]
""" A workspace diagnostic document report.

@since 3.17.0 """

TextDocumentContentChangeEvent = Union["__TextDocumentContentChangeEvent_Type_1", "__TextDocumentContentChangeEvent_Type_2"]
""" An event describing a change to a text document. If only a text is provided
it is considered to be the full content of the document. """

MarkedString = Union[str, "__MarkedString_Type_1"]
""" MarkedString can be used to render human readable text. It is either a markdown string
or a code-block that provides a language and a code snippet. The language identifier
is semantically equal to the optional language identifier in fenced code blocks in GitHub
issues. See https://help.github.com/articles/creating-and-highlighting-code-blocks/#syntax-highlighting

The pair of a language and a value is an equivalent to markdown:
```${language}
${value}
```

Note that markdown strings will be sanitized - that means html will be escaped.
@deprecated use MarkupContent instead. """

DocumentFilter = Union["TextDocumentFilter", "NotebookCellTextDocumentFilter"]
""" A document filter describes a top level text document or
a notebook cell document.

@since 3.17.0 - proposed support for NotebookCellTextDocumentFilter. """

LSPObject = dict[str, "LSPAny"]
""" LSP object definition.
@since 3.17.0 """

GlobPattern = Union["Pattern", "RelativePattern"]
""" The glob pattern. Either a string pattern or a relative pattern.

@since 3.17.0 """

TextDocumentFilter = Union[
    "__TextDocumentFilter_Type_1",
    "__TextDocumentFilter_Type_2",
    "__TextDocumentFilter_Type_3",
]
""" A document filter denotes a document by different properties like
the {@link TextDocument.languageId language}, the {@link Uri.scheme scheme} of
its resource, or a glob-pattern that is applied to the {@link TextDocument.fileName path}.

Glob patterns can have the following syntax:
- `*` to match one or more characters in a path segment
- `?` to match on one character in a path segment
- `**` to match any number of path segments, including none
- `{}` to group sub patterns into an OR expression. (e.g. `**\u200b/*.{ts,js}` matches all TypeScript and JavaScript files)
- `[]` to declare a range of characters to match in a path segment (e.g., `example.[0-9]` to match on `example.0`, `example.1`, …)
- `[!...]` to negate a range of characters to match in a path segment (e.g., `example.[!0-9]` to match on `example.a`, `example.b`, but not `example.0`)

@sample A language filter that applies to typescript files on disk: `{ language: 'typescript', scheme: 'file' }`
@sample A language filter that applies to all package.json paths: `{ language: 'json', pattern: '**package.json' }`

@since 3.17.0 """

NotebookDocumentFilter = Union[
    "__NotebookDocumentFilter_Type_1",
    "__NotebookDocumentFilter_Type_2",
    "__NotebookDocumentFilter_Type_3",
]
""" A notebook document filter denotes a notebook document by
different properties. The properties will be match
against the notebook's URI (same as with documents)

@since 3.17.0 """

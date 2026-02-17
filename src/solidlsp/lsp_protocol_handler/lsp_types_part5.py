from __future__ import annotations

from enum import Enum, IntEnum, IntFlag
from typing import Literal, NotRequired, Union

from typing_extensions import TypedDict

class StaticRegistrationOptions(TypedDict):
    """Static registration options to be returned in the initialize
    request.
    """

    id: NotRequired[str]
    """ The id used to register the request. The id can be used to deregister
    the request again. See also Registration#id. """


class TypeDefinitionOptions(TypedDict):
    workDoneProgress: NotRequired[bool]


class WorkspaceFoldersChangeEvent(TypedDict):
    """The workspace folder change event."""

    added: list["WorkspaceFolder"]
    """ The array of added workspace folders """
    removed: list["WorkspaceFolder"]
    """ The array of the removed workspace folders """


class ConfigurationItem(TypedDict):
    scopeUri: NotRequired[str]
    """ The scope to get the configuration section for. """
    section: NotRequired[str]
    """ The configuration section asked for. """


class TextDocumentIdentifier(TypedDict):
    """A literal to identify a text document in the client."""

    uri: "DocumentUri"
    """ The text document's uri. """


class Color(TypedDict):
    """Represents a color in RGBA space."""

    red: float
    """ The red component of this color in the range [0-1]. """
    green: float
    """ The green component of this color in the range [0-1]. """
    blue: float
    """ The blue component of this color in the range [0-1]. """
    alpha: float
    """ The alpha component of this color in the range [0-1]. """


class DocumentColorOptions(TypedDict):
    workDoneProgress: NotRequired[bool]


class FoldingRangeOptions(TypedDict):
    workDoneProgress: NotRequired[bool]


class DeclarationOptions(TypedDict):
    workDoneProgress: NotRequired[bool]


class Position(TypedDict):
    r"""Position in a text document expressed as zero-based line and character
    offset. Prior to 3.17 the offsets were always based on a UTF-16 string
    representation. So a string of the form `a𐐀b` the character offset of the
    character `a` is 0, the character offset of `𐐀` is 1 and the character
    offset of b is 3 since `𐐀` is represented using two code units in UTF-16.
    Since 3.17 clients and servers can agree on a different string encoding
    representation (e.g. UTF-8). The client announces it's supported encoding
    via the client capability [`general.positionEncodings`](#clientCapabilities).
    The value is an array of position encodings the client supports, with
    decreasing preference (e.g. the encoding at index `0` is the most preferred
    one). To stay backwards compatible the only mandatory encoding is UTF-16
    represented via the string `utf-16`. The server can pick one of the
    encodings offered by the client and signals that encoding back to the
    client via the initialize result's property
    [`capabilities.positionEncoding`](#serverCapabilities). If the string value
    `utf-16` is missing from the client's capability `general.positionEncodings`
    servers can safely assume that the client supports UTF-16. If the server
    omits the position encoding in its initialize result the encoding defaults
    to the string value `utf-16`. Implementation considerations: since the
    conversion from one encoding into another requires the content of the
    file / line the conversion is best done where the file is read which is
    usually on the server side.

    Positions are line end character agnostic. So you can not specify a position
    that denotes `\r|\n` or `\n|` where `|` represents the character offset.

    @since 3.17.0 - support for negotiated position encoding.
    """

    line: Uint
    """ Line position in a document (zero-based).

    If a line number is greater than the number of lines in a document, it defaults back to the number of lines in the document.
    If a line number is negative, it defaults to 0. """
    character: Uint
    """ Character offset on a line in a document (zero-based).

    The meaning of this offset is determined by the negotiated
    `PositionEncodingKind`.

    If the character value is greater than the line length it defaults back to the
    line length. """


class SelectionRangeOptions(TypedDict):
    workDoneProgress: NotRequired[bool]


class CallHierarchyOptions(TypedDict):
    """Call hierarchy options used during static registration.

    @since 3.16.0
    """

    workDoneProgress: NotRequired[bool]


class SemanticTokensOptions(TypedDict):
    """@since 3.16.0"""

    legend: "SemanticTokensLegend"
    """ The legend used by the server """
    range: NotRequired[bool | dict]
    """ Server supports providing semantic tokens for a specific range
    of a document. """
    full: NotRequired[Union[bool, "__SemanticTokensOptions_full_Type_2"]]
    """ Server supports providing semantic tokens for a full document. """
    workDoneProgress: NotRequired[bool]


class SemanticTokensEdit(TypedDict):
    """@since 3.16.0"""

    start: Uint
    """ The start offset of the edit. """
    deleteCount: Uint
    """ The count of elements to remove. """
    data: NotRequired[list[Uint]]
    """ The elements to insert. """


class LinkedEditingRangeOptions(TypedDict):
    workDoneProgress: NotRequired[bool]


class FileCreate(TypedDict):
    """Represents information on a file/folder create.

    @since 3.16.0
    """

    uri: str
    """ A file:// URI for the location of the file/folder being created. """


class TextDocumentEdit(TypedDict):
    """Describes textual changes on a text document. A TextDocumentEdit describes all changes
    on a document version Si and after they are applied move the document to version Si+1.
    So the creator of a TextDocumentEdit doesn't need to sort the array of edits or do any
    kind of ordering. However the edits must be non overlapping.
    """

    textDocument: "OptionalVersionedTextDocumentIdentifier"
    """ The text document to change. """
    edits: list[Union["TextEdit", "AnnotatedTextEdit"]]
    """ The edits to be applied.

    @since 3.16.0 - support for AnnotatedTextEdit. This is guarded using a
    client capability. """


class CreateFile(TypedDict):
    """Create file operation."""

    kind: Literal["create"]
    """ A create """
    uri: "DocumentUri"
    """ The resource to create. """
    options: NotRequired["CreateFileOptions"]
    """ Additional options """
    annotationId: NotRequired["ChangeAnnotationIdentifier"]
    """ An optional annotation identifier describing the operation.

    @since 3.16.0 """


class RenameFile(TypedDict):
    """Rename file operation"""

    kind: Literal["rename"]
    """ A rename """
    oldUri: "DocumentUri"
    """ The old (existing) location. """
    newUri: "DocumentUri"
    """ The new location. """
    options: NotRequired["RenameFileOptions"]
    """ Rename options. """
    annotationId: NotRequired["ChangeAnnotationIdentifier"]
    """ An optional annotation identifier describing the operation.

    @since 3.16.0 """


class DeleteFile(TypedDict):
    """Delete file operation"""

    kind: Literal["delete"]
    """ A delete """
    uri: "DocumentUri"
    """ The file to delete. """
    options: NotRequired["DeleteFileOptions"]
    """ Delete options. """
    annotationId: NotRequired["ChangeAnnotationIdentifier"]
    """ An optional annotation identifier describing the operation.

    @since 3.16.0 """


class ChangeAnnotation(TypedDict):
    """Additional information that describes document changes.

    @since 3.16.0
    """

    label: str
    """ A human-readable string describing the actual change. The string
    is rendered prominent in the user interface. """
    needsConfirmation: NotRequired[bool]
    """ A flag which indicates that user confirmation is needed
    before applying the change. """
    description: NotRequired[str]
    """ A human-readable string which is rendered less prominent in
    the user interface. """


class FileOperationFilter(TypedDict):
    """A filter to describe in which file operation requests or notifications
    the server is interested in receiving.

    @since 3.16.0
    """

    scheme: NotRequired[str]
    """ A Uri scheme like `file` or `untitled`. """
    pattern: "FileOperationPattern"
    """ The actual file operation pattern. """


class FileRename(TypedDict):
    """Represents information on a file/folder rename.

    @since 3.16.0
    """

    oldUri: str
    """ A file:// URI for the original location of the file/folder being renamed. """
    newUri: str
    """ A file:// URI for the new location of the file/folder being renamed. """


class FileDelete(TypedDict):
    """Represents information on a file/folder delete.

    @since 3.16.0
    """

    uri: str
    """ A file:// URI for the location of the file/folder being deleted. """


class MonikerOptions(TypedDict):
    workDoneProgress: NotRequired[bool]


class TypeHierarchyOptions(TypedDict):
    """Type hierarchy options used during static registration.

    @since 3.17.0
    """

    workDoneProgress: NotRequired[bool]


class InlineValueContext(TypedDict):
    """@since 3.17.0"""

    frameId: int
    """ The stack frame (as a DAP Id) where the execution has stopped. """
    stoppedLocation: "Range"
    """ The document range where execution has stopped.
    Typically the end position of the range denotes the line where the inline values are shown. """


class InlineValueText(TypedDict):
    """Provide inline value as text.

    @since 3.17.0
    """

    range: "Range"
    """ The document range for which the inline value applies. """
    text: str
    """ The text of the inline value. """


class InlineValueVariableLookup(TypedDict):
    """Provide inline value through a variable lookup.
    If only a range is specified, the variable name will be extracted from the underlying document.
    An optional variable name can be used to override the extracted name.

    @since 3.17.0
    """

    range: "Range"
    """ The document range for which the inline value applies.
    The range is used to extract the variable name from the underlying document. """
    variableName: NotRequired[str]
    """ If specified the name of the variable to look up. """
    caseSensitiveLookup: bool
    """ How to perform the lookup. """


class InlineValueEvaluatableExpression(TypedDict):
    """Provide an inline value through an expression evaluation.
    If only a range is specified, the expression will be extracted from the underlying document.
    An optional expression can be used to override the extracted expression.

    @since 3.17.0
    """

    range: "Range"
    """ The document range for which the inline value applies.
    The range is used to extract the evaluatable expression from the underlying document. """
    expression: NotRequired[str]
    """ If specified the expression overrides the extracted expression. """


class InlineValueOptions(TypedDict):
    """Inline value options used during static registration.

    @since 3.17.0
    """

    workDoneProgress: NotRequired[bool]


class InlayHintLabelPart(TypedDict):
    """An inlay hint label part allows for interactive and composite labels
    of inlay hints.

    @since 3.17.0
    """

    value: str
    """ The value of this label part. """
    tooltip: NotRequired[Union[str, "MarkupContent"]]
    """ The tooltip text when you hover over this label part. Depending on
    the client capability `inlayHint.resolveSupport` clients might resolve
    this property late using the resolve request. """
    location: NotRequired["Location"]
    """ An optional source code location that represents this
    label part.

    The editor will use this location for the hover and for code navigation
    features: This part will become a clickable link that resolves to the
    definition of the symbol at the given location (not necessarily the
    location itself), it shows the hover that shows at the given location,
    and it shows a context menu with further code navigation commands.

    Depending on the client capability `inlayHint.resolveSupport` clients
    might resolve this property late using the resolve request. """
    command: NotRequired["Command"]
    """ An optional command for this label part.

    Depending on the client capability `inlayHint.resolveSupport` clients
    might resolve this property late using the resolve request. """


class MarkupContent(TypedDict):
    r"""A `MarkupContent` literal represents a string value which content is interpreted base on its
    kind flag. Currently the protocol supports `plaintext` and `markdown` as markup kinds.

    If the kind is `markdown` then the value can contain fenced code blocks like in GitHub issues.
    See https://help.github.com/articles/creating-and-highlighting-code-blocks/#syntax-highlighting

    Here is an example how such a string can be constructed using JavaScript / TypeScript:
    ```ts
    let markdown: MarkdownContent = {
     kind: MarkupKind.Markdown,
     value: [
       '# Header',
       'Some text',
       '```typescript',
       'someCode();',
       '```'
     ].join('\n')
    };
    ```

    *Please Note* that clients might sanitize the return markdown. A client could decide to
    remove HTML from the markdown to avoid script execution.
    """

    kind: "MarkupKind"
    """ The type of the Markup """
    value: str
    """ The content itself """


class InlayHintOptions(TypedDict):
    """Inlay hint options used during static registration.

    @since 3.17.0
    """

    resolveProvider: NotRequired[bool]
    """ The server provides support to resolve additional
    information for an inlay hint item. """
    workDoneProgress: NotRequired[bool]


class RelatedFullDocumentDiagnosticReport(TypedDict):
    """A full diagnostic report with a set of related documents.

    @since 3.17.0
    """

    relatedDocuments: NotRequired[
        dict[
            "DocumentUri",
            Union["FullDocumentDiagnosticReport", "UnchangedDocumentDiagnosticReport"],
        ]
    ]
    """ Diagnostics of related documents. This information is useful
    in programming languages where code in a file A can generate
    diagnostics in a file B which A depends on. An example of
    such a language is C/C++ where marco definitions in a file
    a.cpp and result in errors in a header file b.hpp.

    @since 3.17.0 """
    kind: Literal["full"]
    """ A full document diagnostic report. """
    resultId: NotRequired[str]
    """ An optional result id. If provided it will
    be sent on the next diagnostic request for the
    same document. """
    items: list["Diagnostic"]
    """ The actual items. """


class RelatedUnchangedDocumentDiagnosticReport(TypedDict):
    """An unchanged diagnostic report with a set of related documents.

    @since 3.17.0
    """

    relatedDocuments: NotRequired[
        dict[
            "DocumentUri",
            Union["FullDocumentDiagnosticReport", "UnchangedDocumentDiagnosticReport"],
        ]
    ]
    """ Diagnostics of related documents. This information is useful
    in programming languages where code in a file A can generate
    diagnostics in a file B which A depends on. An example of
    such a language is C/C++ where marco definitions in a file
    a.cpp and result in errors in a header file b.hpp.

    @since 3.17.0 """
    kind: Literal["unchanged"]
    """ A document diagnostic report indicating
    no changes to the last result. A server can
    only return `unchanged` if result ids are
    provided. """
    resultId: str
    """ A result id which will be sent on the next
    diagnostic request for the same document. """


class FullDocumentDiagnosticReport(TypedDict):
    """A diagnostic report with a full set of problems.

    @since 3.17.0
    """

    kind: Literal["full"]
    """ A full document diagnostic report. """
    resultId: NotRequired[str]
    """ An optional result id. If provided it will
    be sent on the next diagnostic request for the
    same document. """
    items: list["Diagnostic"]
    """ The actual items. """


class UnchangedDocumentDiagnosticReport(TypedDict):
    """A diagnostic report indicating that the last returned
    report is still accurate.

    @since 3.17.0
    """

    kind: Literal["unchanged"]
    """ A document diagnostic report indicating
    no changes to the last result. A server can
    only return `unchanged` if result ids are
    provided. """
    resultId: str
    """ A result id which will be sent on the next
    diagnostic request for the same document. """


class DiagnosticOptions(TypedDict):
    """Diagnostic options.

    @since 3.17.0
    """

    identifier: NotRequired[str]
    """ An optional identifier under which the diagnostics are
    managed by the client. """
    interFileDependencies: bool
    """ Whether the language has inter file dependencies meaning that
    editing code in one file can result in a different diagnostic
    set in another file. Inter file dependencies are common for
    most programming languages and typically uncommon for linters. """
    workspaceDiagnostics: bool
    """ The server provides support for workspace diagnostics as well. """
    workDoneProgress: NotRequired[bool]


class PreviousResultId(TypedDict):
    """A previous result id in a workspace pull request.

    @since 3.17.0
    """

    uri: "DocumentUri"
    """ The URI for which the client knowns a
    result id. """
    value: str
    """ The value of the previous result id. """


class NotebookDocument(TypedDict):
    """A notebook document.

    @since 3.17.0
    """

    uri: "URI"
    """ The notebook document's uri. """
    notebookType: str
    """ The type of the notebook. """
    version: int
    """ The version number of this document (it will increase after each
    change, including undo/redo). """
    metadata: NotRequired["LSPObject"]
    """ Additional metadata stored with the notebook
    document.

    Note: should always be an object literal (e.g. LSPObject) """
    cells: list["NotebookCell"]
    """ The cells of a notebook. """


class TextDocumentItem(TypedDict):
    """An item to transfer a text document from the client to the
    server.
    """

    uri: "DocumentUri"
    """ The text document's uri. """
    languageId: str
    """ The text document's language identifier. """
    version: int
    """ The version number of this document (it will increase after each
    change, including undo/redo). """
    text: str
    """ The content of the opened text document. """


class VersionedNotebookDocumentIdentifier(TypedDict):
    """A versioned notebook document identifier.

    @since 3.17.0
    """

    version: int
    """ The version number of this notebook document. """
    uri: "URI"
    """ The notebook document's uri. """


class NotebookDocumentChangeEvent(TypedDict):
    """A change event for a notebook document.

    @since 3.17.0
    """

    metadata: NotRequired["LSPObject"]
    """ The changed meta data if any.

    Note: should always be an object literal (e.g. LSPObject) """
    cells: NotRequired["__NotebookDocumentChangeEvent_cells_Type_1"]
    """ Changes to cells """


class NotebookDocumentIdentifier(TypedDict):
    """A literal to identify a notebook document in the client.

    @since 3.17.0
    """

    uri: "URI"
    """ The notebook document's uri. """


class Registration(TypedDict):
    """General parameters to to register for an notification or to register a provider."""

    id: str
    """ The id used to register the request. The id can be used to deregister
    the request again. """
    method: str
    """ The method / capability to register for. """
    registerOptions: NotRequired["LSPAny"]
    """ Options necessary for the registration. """


class Unregistration(TypedDict):
    """General parameters to unregister a request or notification."""

    id: str
    """ The id used to unregister the request or notification. Usually an id
    provided during the register request. """
    method: str
    """ The method to unregister for. """


class WorkspaceFoldersInitializeParams(TypedDict):
    workspaceFolders: NotRequired[list["WorkspaceFolder"] | None]
    """ The workspace folders configured in the client when the server starts.

    This property is only available if the client supports workspace folders.
    It can be `null` if the client supports workspace folders but none are
    configured.

    @since 3.6.0 """



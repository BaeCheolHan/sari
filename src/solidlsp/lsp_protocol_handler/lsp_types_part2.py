from __future__ import annotations

from enum import Enum, IntEnum, IntFlag
from typing import Literal, NotRequired, Union

from typing_extensions import TypedDict

Pattern = str
""" The glob pattern to watch relative to the base path. Glob patterns can have the following syntax:
- `*` to match one or more characters in a path segment
- `?` to match on one character in a path segment
- `**` to match any number of path segments, including none
- `{}` to group conditions (e.g. `**\u200b/*.{ts,js}` matches all TypeScript and JavaScript files)
- `[]` to declare a range of characters to match in a path segment (e.g., `example.[0-9]` to match on `example.0`, `example.1`, …)
- `[!...]` to negate a range of characters to match in a path segment (e.g., `example.[!0-9]` to match on `example.a`, `example.b`, but not `example.0`)

@since 3.17.0 """


class ImplementationParams(TypedDict):
    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    position: "Position"
    """ The position inside the text document. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class Location(TypedDict):
    """Represents a location inside a resource, such as a line
    inside a text file.
    """

    uri: "DocumentUri"
    range: "Range"


class ImplementationRegistrationOptions(TypedDict):
    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """
    id: NotRequired[str]
    """ The id used to register the request. The id can be used to deregister
    the request again. See also Registration#id. """


class TypeDefinitionParams(TypedDict):
    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    position: "Position"
    """ The position inside the text document. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class TypeDefinitionRegistrationOptions(TypedDict):
    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """
    id: NotRequired[str]
    """ The id used to register the request. The id can be used to deregister
    the request again. See also Registration#id. """


class WorkspaceFolder(TypedDict):
    """A workspace folder inside a client."""

    uri: "URI"
    """ The associated URI for this workspace folder. """
    name: str
    """ The name of the workspace folder. Used to refer to this
    workspace folder in the user interface. """


class DidChangeWorkspaceFoldersParams(TypedDict):
    """The parameters of a `workspace/didChangeWorkspaceFolders` notification."""

    event: "WorkspaceFoldersChangeEvent"
    """ The actual workspace folder change event. """


class ConfigurationParams(TypedDict):
    """The parameters of a configuration request."""

    items: list["ConfigurationItem"]


class DocumentColorParams(TypedDict):
    """Parameters for a {@link DocumentColorRequest}."""

    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class ColorInformation(TypedDict):
    """Represents a color range from a document."""

    range: "Range"
    """ The range in the document where this color appears. """
    color: "Color"
    """ The actual color value for this color range. """


class DocumentColorRegistrationOptions(TypedDict):
    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """
    id: NotRequired[str]
    """ The id used to register the request. The id can be used to deregister
    the request again. See also Registration#id. """


class ColorPresentationParams(TypedDict):
    """Parameters for a {@link ColorPresentationRequest}."""

    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    color: "Color"
    """ The color to request presentations for. """
    range: "Range"
    """ The range where the color would be inserted. Serves as a context. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class ColorPresentation(TypedDict):
    label: str
    """ The label of this color presentation. It will be shown on the color
    picker header. By default this is also the text that is inserted when selecting
    this color presentation. """
    textEdit: NotRequired["TextEdit"]
    """ An {@link TextEdit edit} which is applied to a document when selecting
    this presentation for the color.  When `falsy` the {@link ColorPresentation.label label}
    is used. """
    additionalTextEdits: NotRequired[list["TextEdit"]]
    """ An optional array of additional {@link TextEdit text edits} that are applied when
    selecting this color presentation. Edits must not overlap with the main {@link ColorPresentation.textEdit edit} nor with themselves. """


class WorkDoneProgressOptions(TypedDict):
    workDoneProgress: NotRequired[bool]


class TextDocumentRegistrationOptions(TypedDict):
    """General text document registration options."""

    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """


class FoldingRangeParams(TypedDict):
    """Parameters for a {@link FoldingRangeRequest}."""

    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class FoldingRange(TypedDict):
    """Represents a folding range. To be valid, start and end line must be bigger than zero and smaller
    than the number of lines in the document. Clients are free to ignore invalid ranges.
    """

    startLine: Uint
    """ The zero-based start line of the range to fold. The folded area starts after the line's last character.
    To be valid, the end must be zero or larger and smaller than the number of lines in the document. """
    startCharacter: NotRequired[Uint]
    """ The zero-based character offset from where the folded range starts. If not defined, defaults to the length of the start line. """
    endLine: Uint
    """ The zero-based end line of the range to fold. The folded area ends with the line's last character.
    To be valid, the end must be zero or larger and smaller than the number of lines in the document. """
    endCharacter: NotRequired[Uint]
    """ The zero-based character offset before the folded range ends. If not defined, defaults to the length of the end line. """
    kind: NotRequired["FoldingRangeKind"]
    """ Describes the kind of the folding range such as `comment' or 'region'. The kind
    is used to categorize folding ranges and used by commands like 'Fold all comments'.
    See {@link FoldingRangeKind} for an enumeration of standardized kinds. """
    collapsedText: NotRequired[str]
    """ The text that the client should show when the specified range is
    collapsed. If not defined or not supported by the client, a default
    will be chosen by the client.

    @since 3.17.0 """


class FoldingRangeRegistrationOptions(TypedDict):
    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """
    id: NotRequired[str]
    """ The id used to register the request. The id can be used to deregister
    the request again. See also Registration#id. """


class DeclarationParams(TypedDict):
    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    position: "Position"
    """ The position inside the text document. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class DeclarationRegistrationOptions(TypedDict):
    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """
    id: NotRequired[str]
    """ The id used to register the request. The id can be used to deregister
    the request again. See also Registration#id. """


class SelectionRangeParams(TypedDict):
    """A parameter literal used in selection range requests."""

    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    positions: list["Position"]
    """ The positions inside the text document. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class SelectionRange(TypedDict):
    """A selection range represents a part of a selection hierarchy. A selection range
    may have a parent selection range that contains it.
    """

    range: "Range"
    """ The {@link Range range} of this selection range. """
    parent: NotRequired["SelectionRange"]
    """ The parent selection range containing this range. Therefore `parent.range` must contain `this.range`. """


class SelectionRangeRegistrationOptions(TypedDict):
    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """
    id: NotRequired[str]
    """ The id used to register the request. The id can be used to deregister
    the request again. See also Registration#id. """


class WorkDoneProgressCreateParams(TypedDict):
    token: "ProgressToken"
    """ The token to be used to report progress. """


class WorkDoneProgressCancelParams(TypedDict):
    token: "ProgressToken"
    """ The token to be used to report progress. """


class CallHierarchyPrepareParams(TypedDict):
    """The parameter of a `textDocument/prepareCallHierarchy` request.

    @since 3.16.0
    """

    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    position: "Position"
    """ The position inside the text document. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """


class CallHierarchyItem(TypedDict):
    """Represents programming constructs like functions or constructors in the context
    of call hierarchy.

    @since 3.16.0
    """

    name: str
    """ The name of this item. """
    kind: "SymbolKind"
    """ The kind of this item. """
    tags: NotRequired[list["SymbolTag"]]
    """ Tags for this item. """
    detail: NotRequired[str]
    """ More detail for this item, e.g. the signature of a function. """
    uri: "DocumentUri"
    """ The resource identifier of this item. """
    range: "Range"
    """ The range enclosing this symbol not including leading/trailing whitespace but everything else, e.g. comments and code. """
    selectionRange: "Range"
    """ The range that should be selected and revealed when this symbol is being picked, e.g. the name of a function.
    Must be contained by the {@link CallHierarchyItem.range `range`}. """
    data: NotRequired["LSPAny"]
    """ A data entry field that is preserved between a call hierarchy prepare and
    incoming calls or outgoing calls requests. """


class CallHierarchyRegistrationOptions(TypedDict):
    """Call hierarchy options used during static or dynamic registration.

    @since 3.16.0
    """

    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """
    id: NotRequired[str]
    """ The id used to register the request. The id can be used to deregister
    the request again. See also Registration#id. """


class CallHierarchyIncomingCallsParams(TypedDict):
    """The parameter of a `callHierarchy/incomingCalls` request.

    @since 3.16.0
    """

    item: "CallHierarchyItem"
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


CallHierarchyIncomingCall = TypedDict(
    "CallHierarchyIncomingCall",
    {
        # The item that makes the call.
        "from": "CallHierarchyItem",
        # The ranges at which the calls appear. This is relative to the caller
        # denoted by {@link CallHierarchyIncomingCall.from `this.from`}.
        "fromRanges": list["Range"],
    },
)
""" Represents an incoming call, e.g. a caller of a method or constructor.

@since 3.16.0 """


class CallHierarchyOutgoingCallsParams(TypedDict):
    """The parameter of a `callHierarchy/outgoingCalls` request.

    @since 3.16.0
    """

    item: "CallHierarchyItem"
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class CallHierarchyOutgoingCall(TypedDict):
    """Represents an outgoing call, e.g. calling a getter from a method or a method from a constructor etc.

    @since 3.16.0
    """

    to: "CallHierarchyItem"
    """ The item that is called. """
    fromRanges: list["Range"]
    """ The range at which this item is called. This is the range relative to the caller, e.g the item
    passed to {@link CallHierarchyItemProvider.provideCallHierarchyOutgoingCalls `provideCallHierarchyOutgoingCalls`}
    and not {@link CallHierarchyOutgoingCall.to `this.to`}. """


class SemanticTokensParams(TypedDict):
    """@since 3.16.0"""

    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class SemanticTokens(TypedDict):
    """@since 3.16.0"""

    resultId: NotRequired[str]
    """ An optional result id. If provided and clients support delta updating
    the client will include the result id in the next semantic token request.
    A server can then instead of computing all semantic tokens again simply
    send a delta. """
    data: list[Uint]
    """ The actual tokens. """


class SemanticTokensPartialResult(TypedDict):
    """@since 3.16.0"""

    data: list[Uint]


class SemanticTokensRegistrationOptions(TypedDict):
    """@since 3.16.0"""

    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """
    legend: "SemanticTokensLegend"
    """ The legend used by the server """
    range: NotRequired[bool | dict]
    """ Server supports providing semantic tokens for a specific range
    of a document. """
    full: NotRequired[Union[bool, "__SemanticTokensOptions_full_Type_1"]]
    """ Server supports providing semantic tokens for a full document. """
    id: NotRequired[str]
    """ The id used to register the request. The id can be used to deregister
    the request again. See also Registration#id. """


class SemanticTokensDeltaParams(TypedDict):
    """@since 3.16.0"""

    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    previousResultId: str
    """ The result id of a previous response. The result Id can either point to a full response
    or a delta response depending on what was received last. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class SemanticTokensDelta(TypedDict):
    """@since 3.16.0"""

    resultId: NotRequired[str]
    edits: list["SemanticTokensEdit"]
    """ The semantic token edits to transform a previous result into a new result. """


class SemanticTokensDeltaPartialResult(TypedDict):
    """@since 3.16.0"""

    edits: list["SemanticTokensEdit"]


class SemanticTokensRangeParams(TypedDict):
    """@since 3.16.0"""

    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    range: "Range"
    """ The range the semantic tokens are requested for. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class ShowDocumentParams(TypedDict):
    """Params to show a document.

    @since 3.16.0
    """

    uri: "URI"
    """ The document uri to show. """
    external: NotRequired[bool]
    """ Indicates to show the resource in an external program.
    To show for example `https://code.visualstudio.com/`
    in the default WEB browser set `external` to `true`. """
    takeFocus: NotRequired[bool]
    """ An optional property to indicate whether the editor
    showing the document should take focus or not.
    Clients might ignore this property if an external
    program is started. """
    selection: NotRequired["Range"]
    """ An optional selection range if the document is a text
    document. Clients might ignore the property if an
    external program is started or the file is not a text
    file. """


class ShowDocumentResult(TypedDict):
    """The result of a showDocument request.

    @since 3.16.0
    """

    success: bool
    """ A boolean indicating if the show was successful. """


class LinkedEditingRangeParams(TypedDict):
    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    position: "Position"
    """ The position inside the text document. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """


class LinkedEditingRanges(TypedDict):
    """The result of a linked editing range request.

    @since 3.16.0
    """

    ranges: list["Range"]
    """ A list of ranges that can be edited together. The ranges must have
    identical length and contain identical text content. The ranges cannot overlap. """
    wordPattern: NotRequired[str]
    """ An optional word pattern (regular expression) that describes valid contents for
    the given ranges. If no pattern is provided, the client configuration's word
    pattern will be used. """


class LinkedEditingRangeRegistrationOptions(TypedDict):
    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """
    id: NotRequired[str]
    """ The id used to register the request. The id can be used to deregister
    the request again. See also Registration#id. """


class CreateFilesParams(TypedDict):
    """The parameters sent in notifications/requests for user-initiated creation of
    files.

    @since 3.16.0
    """

    files: list["FileCreate"]
    """ An array of all files/folders created in this operation. """


class WorkspaceEdit(TypedDict):
    """A workspace edit represents changes to many resources managed in the workspace. The edit
    should either provide `changes` or `documentChanges`. If documentChanges are present
    they are preferred over `changes` if the client can handle versioned document edits.

    Since version 3.13.0 a workspace edit can contain resource operations as well. If resource
    operations are present clients need to execute the operations in the order in which they
    are provided. So a workspace edit for example can consist of the following two changes:
    (1) a create file a.txt and (2) a text document edit which insert text into file a.txt.

    An invalid sequence (e.g. (1) delete file a.txt and (2) insert text into file a.txt) will
    cause failure of the operation. How the client recovers from the failure is described by
    the client capability: `workspace.workspaceEdit.failureHandling`
    """

    changes: NotRequired[dict["DocumentUri", list["TextEdit"]]]
    """ Holds changes to existing resources. """
    documentChanges: NotRequired[list[Union["TextDocumentEdit", "CreateFile", "RenameFile", "DeleteFile"]]]
    """ Depending on the client capability `workspace.workspaceEdit.resourceOperations` document changes
    are either an array of `TextDocumentEdit`s to express changes to n different text documents
    where each text document edit addresses a specific version of a text document. Or it can contain
    above `TextDocumentEdit`s mixed with create, rename and delete file / folder operations.

    Whether a client supports versioned document edits is expressed via
    `workspace.workspaceEdit.documentChanges` client capability.

    If a client neither supports `documentChanges` nor `workspace.workspaceEdit.resourceOperations` then
    only plain `TextEdit`s using the `changes` property are supported. """
    changeAnnotations: NotRequired[dict["ChangeAnnotationIdentifier", "ChangeAnnotation"]]
    """ A map of change annotations that can be referenced in `AnnotatedTextEdit`s or create, rename and
    delete file / folder operations.

    Whether clients honor this property depends on the client capability `workspace.changeAnnotationSupport`.

    @since 3.16.0 """


class FileOperationRegistrationOptions(TypedDict):
    """The options to register for file operations.

    @since 3.16.0
    """

    filters: list["FileOperationFilter"]
    """ The actual filters. """


class RenameFilesParams(TypedDict):
    """The parameters sent in notifications/requests for user-initiated renames of
    files.

    @since 3.16.0
    """

    files: list["FileRename"]
    """ An array of all files/folders renamed in this operation. When a folder is renamed, only
    the folder will be included, and not its children. """


class DeleteFilesParams(TypedDict):
    """The parameters sent in notifications/requests for user-initiated deletes of
    files.

    @since 3.16.0
    """

    files: list["FileDelete"]
    """ An array of all files/folders deleted in this operation. """


class MonikerParams(TypedDict):
    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    position: "Position"
    """ The position inside the text document. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class Moniker(TypedDict):
    """Moniker definition to match LSIF 0.5 moniker definition.

    @since 3.16.0
    """

    scheme: str
    """ The scheme of the moniker. For example tsc or .Net """
    identifier: str
    """ The identifier of the moniker. The value is opaque in LSIF however
    schema owners are allowed to define the structure if they want. """
    unique: "UniquenessLevel"
    """ The scope in which the moniker is unique """
    kind: NotRequired["MonikerKind"]
    """ The moniker kind if known. """


class MonikerRegistrationOptions(TypedDict):
    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """


class TypeHierarchyPrepareParams(TypedDict):
    """The parameter of a `textDocument/prepareTypeHierarchy` request.

    @since 3.17.0
    """

    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    position: "Position"
    """ The position inside the text document. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """


class TypeHierarchyItem(TypedDict):
    """@since 3.17.0"""

    name: str
    """ The name of this item. """
    kind: "SymbolKind"
    """ The kind of this item. """
    tags: NotRequired[list["SymbolTag"]]
    """ Tags for this item. """
    detail: NotRequired[str]
    """ More detail for this item, e.g. the signature of a function. """
    uri: "DocumentUri"
    """ The resource identifier of this item. """
    range: "Range"
    """ The range enclosing this symbol not including leading/trailing whitespace
    but everything else, e.g. comments and code. """
    selectionRange: "Range"
    """ The range that should be selected and revealed when this symbol is being
    picked, e.g. the name of a function. Must be contained by the
    {@link TypeHierarchyItem.range `range`}. """
    data: NotRequired["LSPAny"]
    """ A data entry field that is preserved between a type hierarchy prepare and
    supertypes or subtypes requests. It could also be used to identify the
    type hierarchy in the server, helping improve the performance on
    resolving supertypes and subtypes. """


class TypeHierarchyRegistrationOptions(TypedDict):
    """Type hierarchy options used during static or dynamic registration.

    @since 3.17.0
    """

    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """
    id: NotRequired[str]
    """ The id used to register the request. The id can be used to deregister
    the request again. See also Registration#id. """


class TypeHierarchySupertypesParams(TypedDict):
    """The parameter of a `typeHierarchy/supertypes` request.

    @since 3.17.0
    """

    item: "TypeHierarchyItem"
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class TypeHierarchySubtypesParams(TypedDict):
    """The parameter of a `typeHierarchy/subtypes` request.

    @since 3.17.0
    """

    item: "TypeHierarchyItem"
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class InlineValueParams(TypedDict):
    """A parameter literal used in inline value requests.

    @since 3.17.0
    """

    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    range: "Range"
    """ The document range for which inline values should be computed. """
    context: "InlineValueContext"
    """ Additional information about the context in which inline values were
    requested. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """



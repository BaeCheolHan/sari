from __future__ import annotations

from enum import Enum, IntEnum, IntFlag
from typing import Literal, NotRequired, Union

from typing_extensions import TypedDict

class DocumentFormattingClientCapabilities(TypedDict):
    """Client capabilities of a {@link DocumentFormattingRequest}."""

    dynamicRegistration: NotRequired[bool]
    """ Whether formatting supports dynamic registration. """


class DocumentRangeFormattingClientCapabilities(TypedDict):
    """Client capabilities of a {@link DocumentRangeFormattingRequest}."""

    dynamicRegistration: NotRequired[bool]
    """ Whether range formatting supports dynamic registration. """


class DocumentOnTypeFormattingClientCapabilities(TypedDict):
    """Client capabilities of a {@link DocumentOnTypeFormattingRequest}."""

    dynamicRegistration: NotRequired[bool]
    """ Whether on type formatting supports dynamic registration. """


class RenameClientCapabilities(TypedDict):
    dynamicRegistration: NotRequired[bool]
    """ Whether rename supports dynamic registration. """
    prepareSupport: NotRequired[bool]
    """ Client supports testing for validity of rename operations
    before execution.

    @since 3.12.0 """
    prepareSupportDefaultBehavior: NotRequired["PrepareSupportDefaultBehavior"]
    """ Client supports the default behavior result.

    The value indicates the default behavior used by the
    client.

    @since 3.16.0 """
    honorsChangeAnnotations: NotRequired[bool]
    """ Whether the client honors the change annotations in
    text edits and resource operations returned via the
    rename request's workspace edit by for example presenting
    the workspace edit in the user interface and asking
    for confirmation.

    @since 3.16.0 """


class FoldingRangeClientCapabilities(TypedDict):
    dynamicRegistration: NotRequired[bool]
    """ Whether implementation supports dynamic registration for folding range
    providers. If this is set to `true` the client supports the new
    `FoldingRangeRegistrationOptions` return value for the corresponding
    server capability as well. """
    rangeLimit: NotRequired[Uint]
    """ The maximum number of folding ranges that the client prefers to receive
    per document. The value serves as a hint, servers are free to follow the
    limit. """
    lineFoldingOnly: NotRequired[bool]
    """ If set, the client signals that it only supports folding complete lines.
    If set, client will ignore specified `startCharacter` and `endCharacter`
    properties in a FoldingRange. """
    foldingRangeKind: NotRequired["__FoldingRangeClientCapabilities_foldingRangeKind_Type_1"]
    """ Specific options for the folding range kind.

    @since 3.17.0 """
    foldingRange: NotRequired["__FoldingRangeClientCapabilities_foldingRange_Type_1"]
    """ Specific options for the folding range.

    @since 3.17.0 """


class SelectionRangeClientCapabilities(TypedDict):
    dynamicRegistration: NotRequired[bool]
    """ Whether implementation supports dynamic registration for selection range providers. If this is set to `true`
    the client supports the new `SelectionRangeRegistrationOptions` return value for the corresponding server
    capability as well. """


class PublishDiagnosticsClientCapabilities(TypedDict):
    """The publish diagnostic client capabilities."""

    relatedInformation: NotRequired[bool]
    """ Whether the clients accepts diagnostics with related information. """
    tagSupport: NotRequired["__PublishDiagnosticsClientCapabilities_tagSupport_Type_1"]
    """ Client supports the tag property to provide meta data about a diagnostic.
    Clients supporting tags have to handle unknown tags gracefully.

    @since 3.15.0 """
    versionSupport: NotRequired[bool]
    """ Whether the client interprets the version property of the
    `textDocument/publishDiagnostics` notification's parameter.

    @since 3.15.0 """
    codeDescriptionSupport: NotRequired[bool]
    """ Client supports a codeDescription property

    @since 3.16.0 """
    dataSupport: NotRequired[bool]
    """ Whether code action supports the `data` property which is
    preserved between a `textDocument/publishDiagnostics` and
    `textDocument/codeAction` request.

    @since 3.16.0 """


class CallHierarchyClientCapabilities(TypedDict):
    """@since 3.16.0"""

    dynamicRegistration: NotRequired[bool]
    """ Whether implementation supports dynamic registration. If this is set to `true`
    the client supports the new `(TextDocumentRegistrationOptions & StaticRegistrationOptions)`
    return value for the corresponding server capability as well. """


class SemanticTokensClientCapabilities(TypedDict):
    """@since 3.16.0"""

    dynamicRegistration: NotRequired[bool]
    """ Whether implementation supports dynamic registration. If this is set to `true`
    the client supports the new `(TextDocumentRegistrationOptions & StaticRegistrationOptions)`
    return value for the corresponding server capability as well. """
    requests: "__SemanticTokensClientCapabilities_requests_Type_1"
    """ Which requests the client supports and might send to the server
    depending on the server's capability. Please note that clients might not
    show semantic tokens or degrade some of the user experience if a range
    or full request is advertised by the client but not provided by the
    server. If for example the client capability `requests.full` and
    `request.range` are both set to true but the server only provides a
    range provider the client might not render a minimap correctly or might
    even decide to not show any semantic tokens at all. """
    tokenTypes: list[str]
    """ The token types that the client supports. """
    tokenModifiers: list[str]
    """ The token modifiers that the client supports. """
    formats: list["TokenFormat"]
    """ The token formats the clients supports. """
    overlappingTokenSupport: NotRequired[bool]
    """ Whether the client supports tokens that can overlap each other. """
    multilineTokenSupport: NotRequired[bool]
    """ Whether the client supports tokens that can span multiple lines. """
    serverCancelSupport: NotRequired[bool]
    """ Whether the client allows the server to actively cancel a
    semantic token request, e.g. supports returning
    LSPErrorCodes.ServerCancelled. If a server does the client
    needs to retrigger the request.

    @since 3.17.0 """
    augmentsSyntaxTokens: NotRequired[bool]
    """ Whether the client uses semantic tokens to augment existing
    syntax tokens. If set to `true` client side created syntax
    tokens and semantic tokens are both used for colorization. If
    set to `false` the client only uses the returned semantic tokens
    for colorization.

    If the value is `undefined` then the client behavior is not
    specified.

    @since 3.17.0 """


class LinkedEditingRangeClientCapabilities(TypedDict):
    """Client capabilities for the linked editing range request.

    @since 3.16.0
    """

    dynamicRegistration: NotRequired[bool]
    """ Whether implementation supports dynamic registration. If this is set to `true`
    the client supports the new `(TextDocumentRegistrationOptions & StaticRegistrationOptions)`
    return value for the corresponding server capability as well. """


class MonikerClientCapabilities(TypedDict):
    """Client capabilities specific to the moniker request.

    @since 3.16.0
    """

    dynamicRegistration: NotRequired[bool]
    """ Whether moniker supports dynamic registration. If this is set to `true`
    the client supports the new `MonikerRegistrationOptions` return value
    for the corresponding server capability as well. """


class TypeHierarchyClientCapabilities(TypedDict):
    """@since 3.17.0"""

    dynamicRegistration: NotRequired[bool]
    """ Whether implementation supports dynamic registration. If this is set to `true`
    the client supports the new `(TextDocumentRegistrationOptions & StaticRegistrationOptions)`
    return value for the corresponding server capability as well. """


class InlineValueClientCapabilities(TypedDict):
    """Client capabilities specific to inline values.

    @since 3.17.0
    """

    dynamicRegistration: NotRequired[bool]
    """ Whether implementation supports dynamic registration for inline value providers. """


class InlayHintClientCapabilities(TypedDict):
    """Inlay hint client capabilities.

    @since 3.17.0
    """

    dynamicRegistration: NotRequired[bool]
    """ Whether inlay hints support dynamic registration. """
    resolveSupport: NotRequired["__InlayHintClientCapabilities_resolveSupport_Type_1"]
    """ Indicates which properties a client can resolve lazily on an inlay
    hint. """


class DiagnosticClientCapabilities(TypedDict):
    """Client capabilities specific to diagnostic pull requests.

    @since 3.17.0
    """

    dynamicRegistration: NotRequired[bool]
    """ Whether implementation supports dynamic registration. If this is set to `true`
    the client supports the new `(TextDocumentRegistrationOptions & StaticRegistrationOptions)`
    return value for the corresponding server capability as well. """
    relatedDocumentSupport: NotRequired[bool]
    """ Whether the clients supports related documents for document diagnostic pulls. """


class NotebookDocumentSyncClientCapabilities(TypedDict):
    """Notebook specific client capabilities.

    @since 3.17.0
    """

    dynamicRegistration: NotRequired[bool]
    """ Whether implementation supports dynamic registration. If this is
    set to `true` the client supports the new
    `(TextDocumentRegistrationOptions & StaticRegistrationOptions)`
    return value for the corresponding server capability as well. """
    executionSummarySupport: NotRequired[bool]
    """ The client supports sending execution summary data per cell. """


class ShowMessageRequestClientCapabilities(TypedDict):
    """Show message request client capabilities"""

    messageActionItem: NotRequired["__ShowMessageRequestClientCapabilities_messageActionItem_Type_1"]
    """ Capabilities specific to the `MessageActionItem` type. """


class ShowDocumentClientCapabilities(TypedDict):
    """Client capabilities for the showDocument request.

    @since 3.16.0
    """

    support: bool
    """ The client has support for the showDocument
    request. """


class RegularExpressionsClientCapabilities(TypedDict):
    """Client capabilities specific to regular expressions.

    @since 3.16.0
    """

    engine: str
    """ The engine's name. """
    version: NotRequired[str]
    """ The engine's version. """


class MarkdownClientCapabilities(TypedDict):
    """Client capabilities specific to the used markdown parser.

    @since 3.16.0
    """

    parser: str
    """ The name of the parser. """
    version: NotRequired[str]
    """ The version of the parser. """
    allowedTags: NotRequired[list[str]]
    """ A list of HTML tags that the client allows / supports in
    Markdown.

    @since 3.17.0 """


class __CodeActionClientCapabilities_codeActionLiteralSupport_Type_1(TypedDict):
    codeActionKind: "__CodeActionClientCapabilities_codeActionLiteralSupport_codeActionKind_Type_1"
    """ The code action kind is support with the following value
    set. """


class __CodeActionClientCapabilities_codeActionLiteralSupport_codeActionKind_Type_1(TypedDict):
    valueSet: list["CodeActionKind"]
    """ The code action kind values the client supports. When this
    property exists the client also guarantees that it will
    handle values outside its set gracefully and falls back
    to a default value when unknown. """


class __CodeActionClientCapabilities_resolveSupport_Type_1(TypedDict):
    properties: list[str]
    """ The properties that a client can resolve lazily. """


class __CodeAction_disabled_Type_1(TypedDict):
    reason: str
    """ Human readable description of why the code action is currently disabled.

    This is displayed in the code actions UI. """


class __CompletionClientCapabilities_completionItemKind_Type_1(TypedDict):
    valueSet: NotRequired[list["CompletionItemKind"]]
    """ The completion item kind values the client supports. When this
    property exists the client also guarantees that it will
    handle values outside its set gracefully and falls back
    to a default value when unknown.

    If this property is not present the client only supports
    the completion items kinds from `Text` to `Reference` as defined in
    the initial version of the protocol. """


class __CompletionClientCapabilities_completionItem_Type_1(TypedDict):
    snippetSupport: NotRequired[bool]
    """ Client supports snippets as insert text.

    A snippet can define tab stops and placeholders with `$1`, `$2`
    and `${3:foo}`. `$0` defines the final tab stop, it defaults to
    the end of the snippet. Placeholders with equal identifiers are linked,
    that is typing in one will update others too. """
    commitCharactersSupport: NotRequired[bool]
    """ Client supports commit characters on a completion item. """
    documentationFormat: NotRequired[list["MarkupKind"]]
    """ Client supports the following content formats for the documentation
    property. The order describes the preferred format of the client. """
    deprecatedSupport: NotRequired[bool]
    """ Client supports the deprecated property on a completion item. """
    preselectSupport: NotRequired[bool]
    """ Client supports the preselect property on a completion item. """
    tagSupport: NotRequired["__CompletionClientCapabilities_completionItem_tagSupport_Type_1"]
    """ Client supports the tag property on a completion item. Clients supporting
    tags have to handle unknown tags gracefully. Clients especially need to
    preserve unknown tags when sending a completion item back to the server in
    a resolve call.

    @since 3.15.0 """
    insertReplaceSupport: NotRequired[bool]
    """ Client support insert replace edit to control different behavior if a
    completion item is inserted in the text or should replace text.

    @since 3.16.0 """
    resolveSupport: NotRequired["__CompletionClientCapabilities_completionItem_resolveSupport_Type_1"]
    """ Indicates which properties a client can resolve lazily on a completion
    item. Before version 3.16.0 only the predefined properties `documentation`
    and `details` could be resolved lazily.

    @since 3.16.0 """
    insertTextModeSupport: NotRequired["__CompletionClientCapabilities_completionItem_insertTextModeSupport_Type_1"]
    """ The client supports the `insertTextMode` property on
    a completion item to override the whitespace handling mode
    as defined by the client (see `insertTextMode`).

    @since 3.16.0 """
    labelDetailsSupport: NotRequired[bool]
    """ The client has support for completion item label
    details (see also `CompletionItemLabelDetails`).

    @since 3.17.0 """


class __CompletionClientCapabilities_completionItem_insertTextModeSupport_Type_1(TypedDict):
    valueSet: list["InsertTextMode"]


class __CompletionClientCapabilities_completionItem_resolveSupport_Type_1(TypedDict):
    properties: list[str]
    """ The properties that a client can resolve lazily. """


class __CompletionClientCapabilities_completionItem_tagSupport_Type_1(TypedDict):
    valueSet: list["CompletionItemTag"]
    """ The tags supported by the client. """


class __CompletionClientCapabilities_completionList_Type_1(TypedDict):
    itemDefaults: NotRequired[list[str]]
    """ The client supports the following itemDefaults on
    a completion list.

    The value lists the supported property names of the
    `CompletionList.itemDefaults` object. If omitted
    no properties are supported.

    @since 3.17.0 """


class __CompletionList_itemDefaults_Type_1(TypedDict):
    commitCharacters: NotRequired[list[str]]
    """ A default commit character set.

    @since 3.17.0 """
    editRange: NotRequired[Union["Range", "__CompletionList_itemDefaults_editRange_Type_1"]]
    """ A default edit range.

    @since 3.17.0 """
    insertTextFormat: NotRequired["InsertTextFormat"]
    """ A default insert text format.

    @since 3.17.0 """
    insertTextMode: NotRequired["InsertTextMode"]
    """ A default insert text mode.

    @since 3.17.0 """
    data: NotRequired["LSPAny"]
    """ A default data value.

    @since 3.17.0 """


class __CompletionList_itemDefaults_editRange_Type_1(TypedDict):
    insert: "Range"
    replace: "Range"


class __CompletionOptions_completionItem_Type_1(TypedDict):
    labelDetailsSupport: NotRequired[bool]
    """ The server has support for completion item label
    details (see also `CompletionItemLabelDetails`) when
    receiving a completion item in a resolve call.

    @since 3.17.0 """


class __CompletionOptions_completionItem_Type_2(TypedDict):
    labelDetailsSupport: NotRequired[bool]
    """ The server has support for completion item label
    details (see also `CompletionItemLabelDetails`) when
    receiving a completion item in a resolve call.

    @since 3.17.0 """


class __DocumentSymbolClientCapabilities_symbolKind_Type_1(TypedDict):
    valueSet: NotRequired[list["SymbolKind"]]
    """ The symbol kind values the client supports. When this
    property exists the client also guarantees that it will
    handle values outside its set gracefully and falls back
    to a default value when unknown.

    If this property is not present the client only supports
    the symbol kinds from `File` to `Array` as defined in
    the initial version of the protocol. """


class __DocumentSymbolClientCapabilities_tagSupport_Type_1(TypedDict):
    valueSet: list["SymbolTag"]
    """ The tags supported by the client. """


class __FoldingRangeClientCapabilities_foldingRangeKind_Type_1(TypedDict):
    valueSet: NotRequired[list["FoldingRangeKind"]]
    """ The folding range kind values the client supports. When this
    property exists the client also guarantees that it will
    handle values outside its set gracefully and falls back
    to a default value when unknown. """


class __FoldingRangeClientCapabilities_foldingRange_Type_1(TypedDict):
    collapsedText: NotRequired[bool]
    """ If set, the client signals that it supports setting collapsedText on
    folding ranges to display custom labels instead of the default text.

    @since 3.17.0 """


class __GeneralClientCapabilities_staleRequestSupport_Type_1(TypedDict):
    cancel: bool
    """ The client will actively cancel the request. """
    retryOnContentModified: list[str]
    """ The list of requests for which the client
    will retry the request if it receives a
    response with error code `ContentModified` """


class __InitializeResult_serverInfo_Type_1(TypedDict):
    name: str
    """ The name of the server as defined by the server. """
    version: NotRequired[str]
    """ The server's version as defined by the server. """


class __InlayHintClientCapabilities_resolveSupport_Type_1(TypedDict):
    properties: list[str]
    """ The properties that a client can resolve lazily. """


class __MarkedString_Type_1(TypedDict):
    language: str
    value: str


class __NotebookDocumentChangeEvent_cells_Type_1(TypedDict):
    structure: NotRequired["__NotebookDocumentChangeEvent_cells_structure_Type_1"]
    """ Changes to the cell structure to add or
    remove cells. """
    data: NotRequired[list["NotebookCell"]]
    """ Changes to notebook cells properties like its
    kind, execution summary or metadata. """
    textContent: NotRequired[list["__NotebookDocumentChangeEvent_cells_textContent_Type_1"]]
    """ Changes to the text content of notebook cells. """


class __NotebookDocumentChangeEvent_cells_structure_Type_1(TypedDict):
    array: "NotebookCellArrayChange"
    """ The change to the cell array. """
    didOpen: NotRequired[list["TextDocumentItem"]]
    """ Additional opened cell text documents. """
    didClose: NotRequired[list["TextDocumentIdentifier"]]
    """ Additional closed cell text documents. """


class __NotebookDocumentChangeEvent_cells_textContent_Type_1(TypedDict):
    document: "VersionedTextDocumentIdentifier"
    changes: list["TextDocumentContentChangeEvent"]


class __NotebookDocumentFilter_Type_1(TypedDict):
    notebookType: str
    """ The type of the enclosing notebook. """
    scheme: NotRequired[str]
    """ A Uri {@link Uri.scheme scheme}, like `file` or `untitled`. """
    pattern: NotRequired[str]
    """ A glob pattern. """


class __NotebookDocumentFilter_Type_2(TypedDict):
    notebookType: NotRequired[str]
    """ The type of the enclosing notebook. """
    scheme: str
    """ A Uri {@link Uri.scheme scheme}, like `file` or `untitled`. """
    pattern: NotRequired[str]
    """ A glob pattern. """


class __NotebookDocumentFilter_Type_3(TypedDict):
    notebookType: NotRequired[str]
    """ The type of the enclosing notebook. """
    scheme: NotRequired[str]
    """ A Uri {@link Uri.scheme scheme}, like `file` or `untitled`. """
    pattern: str
    """ A glob pattern. """


class __NotebookDocumentSyncOptions_notebookSelector_Type_1(TypedDict):
    notebook: Union[str, "NotebookDocumentFilter"]
    """ The notebook to be synced If a string
    value is provided it matches against the
    notebook type. '*' matches every notebook. """
    cells: NotRequired[list["__NotebookDocumentSyncOptions_notebookSelector_cells_Type_1"]]
    """ The cells of the matching notebook to be synced. """


class __NotebookDocumentSyncOptions_notebookSelector_Type_2(TypedDict):
    notebook: NotRequired[Union[str, "NotebookDocumentFilter"]]
    """ The notebook to be synced If a string
    value is provided it matches against the
    notebook type. '*' matches every notebook. """
    cells: list["__NotebookDocumentSyncOptions_notebookSelector_cells_Type_2"]
    """ The cells of the matching notebook to be synced. """


class __NotebookDocumentSyncOptions_notebookSelector_Type_3(TypedDict):
    notebook: Union[str, "NotebookDocumentFilter"]
    """ The notebook to be synced If a string
    value is provided it matches against the
    notebook type. '*' matches every notebook. """
    cells: NotRequired[list["__NotebookDocumentSyncOptions_notebookSelector_cells_Type_3"]]
    """ The cells of the matching notebook to be synced. """


class __NotebookDocumentSyncOptions_notebookSelector_Type_4(TypedDict):
    notebook: NotRequired[Union[str, "NotebookDocumentFilter"]]
    """ The notebook to be synced If a string
    value is provided it matches against the
    notebook type. '*' matches every notebook. """
    cells: list["__NotebookDocumentSyncOptions_notebookSelector_cells_Type_4"]
    """ The cells of the matching notebook to be synced. """


class __NotebookDocumentSyncOptions_notebookSelector_cells_Type_1(TypedDict):
    language: str


class __NotebookDocumentSyncOptions_notebookSelector_cells_Type_2(TypedDict):
    language: str


class __NotebookDocumentSyncOptions_notebookSelector_cells_Type_3(TypedDict):
    language: str


class __NotebookDocumentSyncOptions_notebookSelector_cells_Type_4(TypedDict):
    language: str


class __PrepareRenameResult_Type_1(TypedDict):
    range: "Range"
    placeholder: str


class __PrepareRenameResult_Type_2(TypedDict):
    defaultBehavior: bool


class __PublishDiagnosticsClientCapabilities_tagSupport_Type_1(TypedDict):
    valueSet: list["DiagnosticTag"]
    """ The tags supported by the client. """


class __SemanticTokensClientCapabilities_requests_Type_1(TypedDict):
    range: NotRequired[bool | dict]
    """ The client will send the `textDocument/semanticTokens/range` request if
    the server provides a corresponding handler. """
    full: NotRequired[Union[bool, "__SemanticTokensClientCapabilities_requests_full_Type_1"]]
    """ The client will send the `textDocument/semanticTokens/full` request if
    the server provides a corresponding handler. """


class __SemanticTokensClientCapabilities_requests_full_Type_1(TypedDict):
    delta: NotRequired[bool]
    """ The client will send the `textDocument/semanticTokens/full/delta` request if
    the server provides a corresponding handler. """


class __SemanticTokensOptions_full_Type_1(TypedDict):
    delta: NotRequired[bool]
    """ The server supports deltas for full documents. """


class __SemanticTokensOptions_full_Type_2(TypedDict):
    delta: NotRequired[bool]
    """ The server supports deltas for full documents. """


class __ServerCapabilities_workspace_Type_1(TypedDict):
    workspaceFolders: NotRequired["WorkspaceFoldersServerCapabilities"]
    """ The server supports workspace folder.

    @since 3.6.0 """
    fileOperations: NotRequired["FileOperationOptions"]
    """ The server is interested in notifications/requests for operations on files.

    @since 3.16.0 """


class __ShowMessageRequestClientCapabilities_messageActionItem_Type_1(TypedDict):
    additionalPropertiesSupport: NotRequired[bool]
    """ Whether the client supports additional attributes which
    are preserved and send back to the server in the
    request's response. """


class __SignatureHelpClientCapabilities_signatureInformation_Type_1(TypedDict):
    documentationFormat: NotRequired[list["MarkupKind"]]
    """ Client supports the following content formats for the documentation
    property. The order describes the preferred format of the client. """
    parameterInformation: NotRequired["__SignatureHelpClientCapabilities_signatureInformation_parameterInformation_Type_1"]
    """ Client capabilities specific to parameter information. """
    activeParameterSupport: NotRequired[bool]
    """ The client supports the `activeParameter` property on `SignatureInformation`
    literal.

    @since 3.16.0 """


class __SignatureHelpClientCapabilities_signatureInformation_parameterInformation_Type_1(TypedDict):
    labelOffsetSupport: NotRequired[bool]
    """ The client supports processing label offsets instead of a
    simple label string.

    @since 3.14.0 """


class __TextDocumentContentChangeEvent_Type_1(TypedDict):
    range: "Range"
    """ The range of the document that changed. """
    rangeLength: NotRequired[Uint]
    """ The optional length of the range that got replaced.

    @deprecated use range instead. """
    text: str
    """ The new text for the provided range. """


class __TextDocumentContentChangeEvent_Type_2(TypedDict):
    text: str
    """ The new text of the whole document. """


class __TextDocumentFilter_Type_1(TypedDict):
    language: str
    """ A language id, like `typescript`. """
    scheme: NotRequired[str]
    """ A Uri {@link Uri.scheme scheme}, like `file` or `untitled`. """
    pattern: NotRequired[str]
    """ A glob pattern, like `*.{ts,js}`. """


class __TextDocumentFilter_Type_2(TypedDict):
    language: NotRequired[str]
    """ A language id, like `typescript`. """
    scheme: str
    """ A Uri {@link Uri.scheme scheme}, like `file` or `untitled`. """
    pattern: NotRequired[str]
    """ A glob pattern, like `*.{ts,js}`. """


class __TextDocumentFilter_Type_3(TypedDict):
    language: NotRequired[str]
    """ A language id, like `typescript`. """
    scheme: NotRequired[str]
    """ A Uri {@link Uri.scheme scheme}, like `file` or `untitled`. """
    pattern: str
    """ A glob pattern, like `*.{ts,js}`. """


class __WorkspaceEditClientCapabilities_changeAnnotationSupport_Type_1(TypedDict):
    groupsOnLabel: NotRequired[bool]
    """ Whether the client groups edits with equal labels into tree nodes,
    for instance all edits labelled with "Changes in Strings" would
    be a tree node. """


class __WorkspaceSymbolClientCapabilities_resolveSupport_Type_1(TypedDict):
    properties: list[str]
    """ The properties that a client can resolve lazily. Usually
    `location.range` """


class __WorkspaceSymbolClientCapabilities_symbolKind_Type_1(TypedDict):
    valueSet: NotRequired[list["SymbolKind"]]
    """ The symbol kind values the client supports. When this
    property exists the client also guarantees that it will
    handle values outside its set gracefully and falls back
    to a default value when unknown.

    If this property is not present the client only supports
    the symbol kinds from `File` to `Array` as defined in
    the initial version of the protocol. """



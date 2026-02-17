from __future__ import annotations

from enum import Enum, IntEnum, IntFlag
from typing import Literal, NotRequired, Union

from typing_extensions import TypedDict

class NotebookDocumentSyncRegistrationOptions(TypedDict):
    """Registration options specific to a notebook.

    @since 3.17.0
    """

    notebookSelector: list[
        Union[
            "__NotebookDocumentSyncOptions_notebookSelector_Type_3",
            "__NotebookDocumentSyncOptions_notebookSelector_Type_4",
        ]
    ]
    """ The notebooks to be synced """
    save: NotRequired[bool]
    """ Whether save notification should be forwarded to
    the server. Will only be honored if mode === `notebook`. """
    id: NotRequired[str]
    """ The id used to register the request. The id can be used to deregister
    the request again. See also Registration#id. """


class WorkspaceFoldersServerCapabilities(TypedDict):
    supported: NotRequired[bool]
    """ The server has support for workspace folders """
    changeNotifications: NotRequired[str | bool]
    """ Whether the server wants to receive workspace folder
    change notifications.

    If a string is provided the string is treated as an ID
    under which the notification is registered on the client
    side. The ID can be used to unregister for these events
    using the `client/unregisterCapability` request. """


class FileOperationOptions(TypedDict):
    """Options for notifications/requests for user operations on files.

    @since 3.16.0
    """

    didCreate: NotRequired["FileOperationRegistrationOptions"]
    """ The server is interested in receiving didCreateFiles notifications. """
    willCreate: NotRequired["FileOperationRegistrationOptions"]
    """ The server is interested in receiving willCreateFiles requests. """
    didRename: NotRequired["FileOperationRegistrationOptions"]
    """ The server is interested in receiving didRenameFiles notifications. """
    willRename: NotRequired["FileOperationRegistrationOptions"]
    """ The server is interested in receiving willRenameFiles requests. """
    didDelete: NotRequired["FileOperationRegistrationOptions"]
    """ The server is interested in receiving didDeleteFiles file notifications. """
    willDelete: NotRequired["FileOperationRegistrationOptions"]
    """ The server is interested in receiving willDeleteFiles file requests. """


class CodeDescription(TypedDict):
    """Structure to capture a description for an error code.

    @since 3.16.0
    """

    href: "URI"
    """ An URI to open with more information about the diagnostic error. """


class DiagnosticRelatedInformation(TypedDict):
    """Represents a related message and source code location for a diagnostic. This should be
    used to point to code locations that cause or related to a diagnostics, e.g when duplicating
    a symbol in a scope.
    """

    location: "Location"
    """ The location of this related diagnostic information. """
    message: str
    """ The message of this related diagnostic information. """


class ParameterInformation(TypedDict):
    """Represents a parameter of a callable-signature. A parameter can
    have a label and a doc-comment.
    """

    label: str | list[Uint | Uint]
    """ The label of this parameter information.

    Either a string or an inclusive start and exclusive end offsets within its containing
    signature label. (see SignatureInformation.label). The offsets are based on a UTF-16
    string representation as `Position` and `Range` does.

    *Note*: a label of type string should be a substring of its containing signature label.
    Its intended use case is to highlight the parameter label part in the `SignatureInformation.label`. """
    documentation: NotRequired[Union[str, "MarkupContent"]]
    """ The human-readable doc-comment of this parameter. Will be shown
    in the UI but can be omitted. """


class NotebookCellTextDocumentFilter(TypedDict):
    """A notebook cell text document filter denotes a cell text
    document by different properties.

    @since 3.17.0
    """

    notebook: Union[str, "NotebookDocumentFilter"]
    """ A filter that matches against the notebook
    containing the notebook cell. If a string
    value is provided it matches against the
    notebook type. '*' matches every notebook. """
    language: NotRequired[str]
    """ A language id like `python`.

    Will be matched against the language id of the
    notebook cell document. '*' matches every language. """


class FileOperationPatternOptions(TypedDict):
    """Matching options for the file operation pattern.

    @since 3.16.0
    """

    ignoreCase: NotRequired[bool]
    """ The pattern should be matched ignoring casing. """


class ExecutionSummary(TypedDict):
    executionOrder: Uint
    """ A strict monotonically increasing value
    indicating the execution order of a cell
    inside a notebook. """
    success: NotRequired[bool]
    """ Whether the execution was successful or
    not if known by the client. """


class WorkspaceClientCapabilities(TypedDict):
    """Workspace specific client capabilities."""

    applyEdit: NotRequired[bool]
    """ The client supports applying batch edits
    to the workspace by supporting the request
    'workspace/applyEdit' """
    workspaceEdit: NotRequired["WorkspaceEditClientCapabilities"]
    """ Capabilities specific to `WorkspaceEdit`s. """
    didChangeConfiguration: NotRequired["DidChangeConfigurationClientCapabilities"]
    """ Capabilities specific to the `workspace/didChangeConfiguration` notification. """
    didChangeWatchedFiles: NotRequired["DidChangeWatchedFilesClientCapabilities"]
    """ Capabilities specific to the `workspace/didChangeWatchedFiles` notification. """
    symbol: NotRequired["WorkspaceSymbolClientCapabilities"]
    """ Capabilities specific to the `workspace/symbol` request. """
    executeCommand: NotRequired["ExecuteCommandClientCapabilities"]
    """ Capabilities specific to the `workspace/executeCommand` request. """
    workspaceFolders: NotRequired[bool]
    """ The client has support for workspace folders.

    @since 3.6.0 """
    configuration: NotRequired[bool]
    """ The client supports `workspace/configuration` requests.

    @since 3.6.0 """
    semanticTokens: NotRequired["SemanticTokensWorkspaceClientCapabilities"]
    """ Capabilities specific to the semantic token requests scoped to the
    workspace.

    @since 3.16.0. """
    codeLens: NotRequired["CodeLensWorkspaceClientCapabilities"]
    """ Capabilities specific to the code lens requests scoped to the
    workspace.

    @since 3.16.0. """
    fileOperations: NotRequired["FileOperationClientCapabilities"]
    """ The client has support for file notifications/requests for user operations on files.

    Since 3.16.0 """
    inlineValue: NotRequired["InlineValueWorkspaceClientCapabilities"]
    """ Capabilities specific to the inline values requests scoped to the
    workspace.

    @since 3.17.0. """
    inlayHint: NotRequired["InlayHintWorkspaceClientCapabilities"]
    """ Capabilities specific to the inlay hint requests scoped to the
    workspace.

    @since 3.17.0. """
    diagnostics: NotRequired["DiagnosticWorkspaceClientCapabilities"]
    """ Capabilities specific to the diagnostic requests scoped to the
    workspace.

    @since 3.17.0. """


class TextDocumentClientCapabilities(TypedDict):
    """Text document specific client capabilities."""

    synchronization: NotRequired["TextDocumentSyncClientCapabilities"]
    """ Defines which synchronization capabilities the client supports. """
    completion: NotRequired["CompletionClientCapabilities"]
    """ Capabilities specific to the `textDocument/completion` request. """
    hover: NotRequired["HoverClientCapabilities"]
    """ Capabilities specific to the `textDocument/hover` request. """
    signatureHelp: NotRequired["SignatureHelpClientCapabilities"]
    """ Capabilities specific to the `textDocument/signatureHelp` request. """
    declaration: NotRequired["DeclarationClientCapabilities"]
    """ Capabilities specific to the `textDocument/declaration` request.

    @since 3.14.0 """
    definition: NotRequired["DefinitionClientCapabilities"]
    """ Capabilities specific to the `textDocument/definition` request. """
    typeDefinition: NotRequired["TypeDefinitionClientCapabilities"]
    """ Capabilities specific to the `textDocument/typeDefinition` request.

    @since 3.6.0 """
    implementation: NotRequired["ImplementationClientCapabilities"]
    """ Capabilities specific to the `textDocument/implementation` request.

    @since 3.6.0 """
    references: NotRequired["ReferenceClientCapabilities"]
    """ Capabilities specific to the `textDocument/references` request. """
    documentHighlight: NotRequired["DocumentHighlightClientCapabilities"]
    """ Capabilities specific to the `textDocument/documentHighlight` request. """
    documentSymbol: NotRequired["DocumentSymbolClientCapabilities"]
    """ Capabilities specific to the `textDocument/documentSymbol` request. """
    codeAction: NotRequired["CodeActionClientCapabilities"]
    """ Capabilities specific to the `textDocument/codeAction` request. """
    codeLens: NotRequired["CodeLensClientCapabilities"]
    """ Capabilities specific to the `textDocument/codeLens` request. """
    documentLink: NotRequired["DocumentLinkClientCapabilities"]
    """ Capabilities specific to the `textDocument/documentLink` request. """
    colorProvider: NotRequired["DocumentColorClientCapabilities"]
    """ Capabilities specific to the `textDocument/documentColor` and the
    `textDocument/colorPresentation` request.

    @since 3.6.0 """
    formatting: NotRequired["DocumentFormattingClientCapabilities"]
    """ Capabilities specific to the `textDocument/formatting` request. """
    rangeFormatting: NotRequired["DocumentRangeFormattingClientCapabilities"]
    """ Capabilities specific to the `textDocument/rangeFormatting` request. """
    onTypeFormatting: NotRequired["DocumentOnTypeFormattingClientCapabilities"]
    """ Capabilities specific to the `textDocument/onTypeFormatting` request. """
    rename: NotRequired["RenameClientCapabilities"]
    """ Capabilities specific to the `textDocument/rename` request. """
    foldingRange: NotRequired["FoldingRangeClientCapabilities"]
    """ Capabilities specific to the `textDocument/foldingRange` request.

    @since 3.10.0 """
    selectionRange: NotRequired["SelectionRangeClientCapabilities"]
    """ Capabilities specific to the `textDocument/selectionRange` request.

    @since 3.15.0 """
    publishDiagnostics: NotRequired["PublishDiagnosticsClientCapabilities"]
    """ Capabilities specific to the `textDocument/publishDiagnostics` notification. """
    callHierarchy: NotRequired["CallHierarchyClientCapabilities"]
    """ Capabilities specific to the various call hierarchy requests.

    @since 3.16.0 """
    semanticTokens: NotRequired["SemanticTokensClientCapabilities"]
    """ Capabilities specific to the various semantic token request.

    @since 3.16.0 """
    linkedEditingRange: NotRequired["LinkedEditingRangeClientCapabilities"]
    """ Capabilities specific to the `textDocument/linkedEditingRange` request.

    @since 3.16.0 """
    moniker: NotRequired["MonikerClientCapabilities"]
    """ Client capabilities specific to the `textDocument/moniker` request.

    @since 3.16.0 """
    typeHierarchy: NotRequired["TypeHierarchyClientCapabilities"]
    """ Capabilities specific to the various type hierarchy requests.

    @since 3.17.0 """
    inlineValue: NotRequired["InlineValueClientCapabilities"]
    """ Capabilities specific to the `textDocument/inlineValue` request.

    @since 3.17.0 """
    inlayHint: NotRequired["InlayHintClientCapabilities"]
    """ Capabilities specific to the `textDocument/inlayHint` request.

    @since 3.17.0 """
    diagnostic: NotRequired["DiagnosticClientCapabilities"]
    """ Capabilities specific to the diagnostic pull model.

    @since 3.17.0 """


class NotebookDocumentClientCapabilities(TypedDict):
    """Capabilities specific to the notebook document support.

    @since 3.17.0
    """

    synchronization: "NotebookDocumentSyncClientCapabilities"
    """ Capabilities specific to notebook document synchronization

    @since 3.17.0 """


class WindowClientCapabilities(TypedDict):
    workDoneProgress: NotRequired[bool]
    """ It indicates whether the client supports server initiated
    progress using the `window/workDoneProgress/create` request.

    The capability also controls Whether client supports handling
    of progress notifications. If set servers are allowed to report a
    `workDoneProgress` property in the request specific server
    capabilities.

    @since 3.15.0 """
    showMessage: NotRequired["ShowMessageRequestClientCapabilities"]
    """ Capabilities specific to the showMessage request.

    @since 3.16.0 """
    showDocument: NotRequired["ShowDocumentClientCapabilities"]
    """ Capabilities specific to the showDocument request.

    @since 3.16.0 """


class GeneralClientCapabilities(TypedDict):
    """General client capabilities.

    @since 3.16.0
    """

    staleRequestSupport: NotRequired["__GeneralClientCapabilities_staleRequestSupport_Type_1"]
    """ Client capability that signals how the client
    handles stale requests (e.g. a request
    for which the client will not process the response
    anymore since the information is outdated).

    @since 3.17.0 """
    regularExpressions: NotRequired["RegularExpressionsClientCapabilities"]
    """ Client capabilities specific to regular expressions.

    @since 3.16.0 """
    markdown: NotRequired["MarkdownClientCapabilities"]
    """ Client capabilities specific to the client's markdown parser.

    @since 3.16.0 """
    positionEncodings: NotRequired[list["PositionEncodingKind"]]
    """ The position encodings supported by the client. Client and server
    have to agree on the same position encoding to ensure that offsets
    (e.g. character position in a line) are interpreted the same on both
    sides.

    To keep the protocol backwards compatible the following applies: if
    the value 'utf-16' is missing from the array of position encodings
    servers can assume that the client supports UTF-16. UTF-16 is
    therefore a mandatory encoding.

    If omitted it defaults to ['utf-16'].

    Implementation considerations: since the conversion from one encoding
    into another requires the content of the file / line the conversion
    is best done where the file is read which is usually on the server
    side.

    @since 3.17.0 """


class RelativePattern(TypedDict):
    """A relative pattern is a helper to construct glob patterns that are matched
    relatively to a base URI. The common value for a `baseUri` is a workspace
    folder root, but it can be another absolute URI as well.

    @since 3.17.0
    """

    baseUri: Union["WorkspaceFolder", "URI"]
    """ A workspace folder or a base URI to which this pattern will be matched
    against relatively. """
    pattern: "Pattern"
    """ The actual glob pattern; """


class WorkspaceEditClientCapabilities(TypedDict):
    documentChanges: NotRequired[bool]
    """ The client supports versioned document changes in `WorkspaceEdit`s """
    resourceOperations: NotRequired[list["ResourceOperationKind"]]
    """ The resource operations the client supports. Clients should at least
    support 'create', 'rename' and 'delete' files and folders.

    @since 3.13.0 """
    failureHandling: NotRequired["FailureHandlingKind"]
    """ The failure handling strategy of a client if applying the workspace edit
    fails.

    @since 3.13.0 """
    normalizesLineEndings: NotRequired[bool]
    """ Whether the client normalizes line endings to the client specific
    setting.
    If set to `true` the client will normalize line ending characters
    in a workspace edit to the client-specified new line
    character.

    @since 3.16.0 """
    changeAnnotationSupport: NotRequired["__WorkspaceEditClientCapabilities_changeAnnotationSupport_Type_1"]
    """ Whether the client in general supports change annotations on text edits,
    create file, rename file and delete file changes.

    @since 3.16.0 """


class DidChangeConfigurationClientCapabilities(TypedDict):
    dynamicRegistration: NotRequired[bool]
    """ Did change configuration notification supports dynamic registration. """


class DidChangeWatchedFilesClientCapabilities(TypedDict):
    dynamicRegistration: NotRequired[bool]
    """ Did change watched files notification supports dynamic registration. Please note
    that the current protocol doesn't support static configuration for file changes
    from the server side. """
    relativePatternSupport: NotRequired[bool]
    """ Whether the client has support for {@link  RelativePattern relative pattern}
    or not.

    @since 3.17.0 """


class WorkspaceSymbolClientCapabilities(TypedDict):
    """Client capabilities for a {@link WorkspaceSymbolRequest}."""

    dynamicRegistration: NotRequired[bool]
    """ Symbol request supports dynamic registration. """
    symbolKind: NotRequired["__WorkspaceSymbolClientCapabilities_symbolKind_Type_1"]
    """ Specific capabilities for the `SymbolKind` in the `workspace/symbol` request. """
    tagSupport: NotRequired["__WorkspaceSymbolClientCapabilities_tagSupport_Type_1"]
    """ The client supports tags on `SymbolInformation`.
    Clients supporting tags have to handle unknown tags gracefully.

    @since 3.16.0 """
    resolveSupport: NotRequired["__WorkspaceSymbolClientCapabilities_resolveSupport_Type_1"]
    """ The client support partial workspace symbols. The client will send the
    request `workspaceSymbol/resolve` to the server to resolve additional
    properties.

    @since 3.17.0 """


class ExecuteCommandClientCapabilities(TypedDict):
    """The client capabilities of a {@link ExecuteCommandRequest}."""

    dynamicRegistration: NotRequired[bool]
    """ Execute command supports dynamic registration. """


class SemanticTokensWorkspaceClientCapabilities(TypedDict):
    """@since 3.16.0"""

    refreshSupport: NotRequired[bool]
    """ Whether the client implementation supports a refresh request sent from
    the server to the client.

    Note that this event is global and will force the client to refresh all
    semantic tokens currently shown. It should be used with absolute care
    and is useful for situation where a server for example detects a project
    wide change that requires such a calculation. """


class CodeLensWorkspaceClientCapabilities(TypedDict):
    """@since 3.16.0"""

    refreshSupport: NotRequired[bool]
    """ Whether the client implementation supports a refresh request sent from the
    server to the client.

    Note that this event is global and will force the client to refresh all
    code lenses currently shown. It should be used with absolute care and is
    useful for situation where a server for example detect a project wide
    change that requires such a calculation. """


class FileOperationClientCapabilities(TypedDict):
    """Capabilities relating to events from file operations by the user in the client.

    These events do not come from the file system, they come from user operations
    like renaming a file in the UI.

    @since 3.16.0
    """

    dynamicRegistration: NotRequired[bool]
    """ Whether the client supports dynamic registration for file requests/notifications. """
    didCreate: NotRequired[bool]
    """ The client has support for sending didCreateFiles notifications. """
    willCreate: NotRequired[bool]
    """ The client has support for sending willCreateFiles requests. """
    didRename: NotRequired[bool]
    """ The client has support for sending didRenameFiles notifications. """
    willRename: NotRequired[bool]
    """ The client has support for sending willRenameFiles requests. """
    didDelete: NotRequired[bool]
    """ The client has support for sending didDeleteFiles notifications. """
    willDelete: NotRequired[bool]
    """ The client has support for sending willDeleteFiles requests. """


class InlineValueWorkspaceClientCapabilities(TypedDict):
    """Client workspace capabilities specific to inline values.

    @since 3.17.0
    """

    refreshSupport: NotRequired[bool]
    """ Whether the client implementation supports a refresh request sent from the
    server to the client.

    Note that this event is global and will force the client to refresh all
    inline values currently shown. It should be used with absolute care and is
    useful for situation where a server for example detects a project wide
    change that requires such a calculation. """


class InlayHintWorkspaceClientCapabilities(TypedDict):
    """Client workspace capabilities specific to inlay hints.

    @since 3.17.0
    """

    refreshSupport: NotRequired[bool]
    """ Whether the client implementation supports a refresh request sent from
    the server to the client.

    Note that this event is global and will force the client to refresh all
    inlay hints currently shown. It should be used with absolute care and
    is useful for situation where a server for example detects a project wide
    change that requires such a calculation. """


class DiagnosticWorkspaceClientCapabilities(TypedDict):
    """Workspace client capabilities specific to diagnostic pull requests.

    @since 3.17.0
    """

    refreshSupport: NotRequired[bool]
    """ Whether the client implementation supports a refresh request sent from
    the server to the client.

    Note that this event is global and will force the client to refresh all
    pulled diagnostics currently shown. It should be used with absolute care and
    is useful for situation where a server for example detects a project wide
    change that requires such a calculation. """


class TextDocumentSyncClientCapabilities(TypedDict):
    dynamicRegistration: NotRequired[bool]
    """ Whether text document synchronization supports dynamic registration. """
    willSave: NotRequired[bool]
    """ The client supports sending will save notifications. """
    willSaveWaitUntil: NotRequired[bool]
    """ The client supports sending a will save request and
    waits for a response providing text edits which will
    be applied to the document before it is saved. """
    didSave: NotRequired[bool]
    """ The client supports did save notifications. """


class CompletionClientCapabilities(TypedDict):
    """Completion client capabilities"""

    dynamicRegistration: NotRequired[bool]
    """ Whether completion supports dynamic registration. """
    completionItem: NotRequired["__CompletionClientCapabilities_completionItem_Type_1"]
    """ The client supports the following `CompletionItem` specific
    capabilities. """
    completionItemKind: NotRequired["__CompletionClientCapabilities_completionItemKind_Type_1"]
    insertTextMode: NotRequired["InsertTextMode"]
    """ Defines how the client handles whitespace and indentation
    when accepting a completion item that uses multi line
    text in either `insertText` or `textEdit`.

    @since 3.17.0 """
    contextSupport: NotRequired[bool]
    """ The client supports to send additional context information for a
    `textDocument/completion` request. """
    completionList: NotRequired["__CompletionClientCapabilities_completionList_Type_1"]
    """ The client supports the following `CompletionList` specific
    capabilities.

    @since 3.17.0 """


class HoverClientCapabilities(TypedDict):
    dynamicRegistration: NotRequired[bool]
    """ Whether hover supports dynamic registration. """
    contentFormat: NotRequired[list["MarkupKind"]]
    """ Client supports the following content formats for the content
    property. The order describes the preferred format of the client. """


class SignatureHelpClientCapabilities(TypedDict):
    """Client Capabilities for a {@link SignatureHelpRequest}."""

    dynamicRegistration: NotRequired[bool]
    """ Whether signature help supports dynamic registration. """
    signatureInformation: NotRequired["__SignatureHelpClientCapabilities_signatureInformation_Type_1"]
    """ The client supports the following `SignatureInformation`
    specific properties. """
    contextSupport: NotRequired[bool]
    """ The client supports to send additional context information for a
    `textDocument/signatureHelp` request. A client that opts into
    contextSupport will also support the `retriggerCharacters` on
    `SignatureHelpOptions`.

    @since 3.15.0 """


class DeclarationClientCapabilities(TypedDict):
    """@since 3.14.0"""

    dynamicRegistration: NotRequired[bool]
    """ Whether declaration supports dynamic registration. If this is set to `true`
    the client supports the new `DeclarationRegistrationOptions` return value
    for the corresponding server capability as well. """
    linkSupport: NotRequired[bool]
    """ The client supports additional metadata in the form of declaration links. """


class DefinitionClientCapabilities(TypedDict):
    """Client Capabilities for a {@link DefinitionRequest}."""

    dynamicRegistration: NotRequired[bool]
    """ Whether definition supports dynamic registration. """
    linkSupport: NotRequired[bool]
    """ The client supports additional metadata in the form of definition links.

    @since 3.14.0 """


class TypeDefinitionClientCapabilities(TypedDict):
    """Since 3.6.0"""

    dynamicRegistration: NotRequired[bool]
    """ Whether implementation supports dynamic registration. If this is set to `true`
    the client supports the new `TypeDefinitionRegistrationOptions` return value
    for the corresponding server capability as well. """
    linkSupport: NotRequired[bool]
    """ The client supports additional metadata in the form of definition links.

    Since 3.14.0 """


class ImplementationClientCapabilities(TypedDict):
    """@since 3.6.0"""

    dynamicRegistration: NotRequired[bool]
    """ Whether implementation supports dynamic registration. If this is set to `true`
    the client supports the new `ImplementationRegistrationOptions` return value
    for the corresponding server capability as well. """
    linkSupport: NotRequired[bool]
    """ The client supports additional metadata in the form of definition links.

    @since 3.14.0 """


class ReferenceClientCapabilities(TypedDict):
    """Client Capabilities for a {@link ReferencesRequest}."""

    dynamicRegistration: NotRequired[bool]
    """ Whether references supports dynamic registration. """


class DocumentHighlightClientCapabilities(TypedDict):
    """Client Capabilities for a {@link DocumentHighlightRequest}."""

    dynamicRegistration: NotRequired[bool]
    """ Whether document highlight supports dynamic registration. """


class DocumentSymbolClientCapabilities(TypedDict):
    """Client Capabilities for a {@link DocumentSymbolRequest}."""

    dynamicRegistration: NotRequired[bool]
    """ Whether document symbol supports dynamic registration. """
    symbolKind: NotRequired["__DocumentSymbolClientCapabilities_symbolKind_Type_1"]
    """ Specific capabilities for the `SymbolKind` in the
    `textDocument/documentSymbol` request. """
    hierarchicalDocumentSymbolSupport: NotRequired[bool]
    """ The client supports hierarchical document symbols. """
    tagSupport: NotRequired["__DocumentSymbolClientCapabilities_tagSupport_Type_1"]
    """ The client supports tags on `SymbolInformation`. Tags are supported on
    `DocumentSymbol` if `hierarchicalDocumentSymbolSupport` is set to true.
    Clients supporting tags have to handle unknown tags gracefully.

    @since 3.16.0 """
    labelSupport: NotRequired[bool]
    """ The client supports an additional label presented in the UI when
    registering a document symbol provider.

    @since 3.16.0 """


class CodeActionClientCapabilities(TypedDict):
    """The Client Capabilities of a {@link CodeActionRequest}."""

    dynamicRegistration: NotRequired[bool]
    """ Whether code action supports dynamic registration. """
    codeActionLiteralSupport: NotRequired["__CodeActionClientCapabilities_codeActionLiteralSupport_Type_1"]
    """ The client support code action literals of type `CodeAction` as a valid
    response of the `textDocument/codeAction` request. If the property is not
    set the request can only return `Command` literals.

    @since 3.8.0 """
    isPreferredSupport: NotRequired[bool]
    """ Whether code action supports the `isPreferred` property.

    @since 3.15.0 """
    disabledSupport: NotRequired[bool]
    """ Whether code action supports the `disabled` property.

    @since 3.16.0 """
    dataSupport: NotRequired[bool]
    """ Whether code action supports the `data` property which is
    preserved between a `textDocument/codeAction` and a
    `codeAction/resolve` request.

    @since 3.16.0 """
    resolveSupport: NotRequired["__CodeActionClientCapabilities_resolveSupport_Type_1"]
    """ Whether the client supports resolving additional code action
    properties via a separate `codeAction/resolve` request.

    @since 3.16.0 """
    honorsChangeAnnotations: NotRequired[bool]
    """ Whether the client honors the change annotations in
    text edits and resource operations returned via the
    `CodeAction#edit` property by for example presenting
    the workspace edit in the user interface and asking
    for confirmation.

    @since 3.16.0 """


class CodeLensClientCapabilities(TypedDict):
    """The client capabilities  of a {@link CodeLensRequest}."""

    dynamicRegistration: NotRequired[bool]
    """ Whether code lens supports dynamic registration. """


class DocumentLinkClientCapabilities(TypedDict):
    """The client capabilities of a {@link DocumentLinkRequest}."""

    dynamicRegistration: NotRequired[bool]
    """ Whether document link supports dynamic registration. """
    tooltipSupport: NotRequired[bool]
    """ Whether the client supports the `tooltip` property on `DocumentLink`.

    @since 3.15.0 """


class DocumentColorClientCapabilities(TypedDict):
    dynamicRegistration: NotRequired[bool]
    """ Whether implementation supports dynamic registration. If this is set to `true`
    the client supports the new `DocumentColorRegistrationOptions` return value
    for the corresponding server capability as well. """



from __future__ import annotations

from enum import Enum, IntEnum, IntFlag
from typing import Literal, NotRequired, Union

from typing_extensions import TypedDict

class ServerCapabilities(TypedDict):
    """Defines the capabilities provided by a language
    server.
    """

    positionEncoding: NotRequired["PositionEncodingKind"]
    """ The position encoding the server picked from the encodings offered
    by the client via the client capability `general.positionEncodings`.

    If the client didn't provide any position encodings the only valid
    value that a server can return is 'utf-16'.

    If omitted it defaults to 'utf-16'.

    @since 3.17.0 """
    textDocumentSync: NotRequired[Union["TextDocumentSyncOptions", "TextDocumentSyncKind"]]
    """ Defines how text documents are synced. Is either a detailed structure
    defining each notification or for backwards compatibility the
    TextDocumentSyncKind number. """
    notebookDocumentSync: NotRequired[Union["NotebookDocumentSyncOptions", "NotebookDocumentSyncRegistrationOptions"]]
    """ Defines how notebook documents are synced.

    @since 3.17.0 """
    completionProvider: NotRequired["CompletionOptions"]
    """ The server provides completion support. """
    hoverProvider: NotRequired[Union[bool, "HoverOptions"]]
    """ The server provides hover support. """
    signatureHelpProvider: NotRequired["SignatureHelpOptions"]
    """ The server provides signature help support. """
    declarationProvider: NotRequired[Union[bool, "DeclarationOptions", "DeclarationRegistrationOptions"]]
    """ The server provides Goto Declaration support. """
    definitionProvider: NotRequired[Union[bool, "DefinitionOptions"]]
    """ The server provides goto definition support. """
    typeDefinitionProvider: NotRequired[Union[bool, "TypeDefinitionOptions", "TypeDefinitionRegistrationOptions"]]
    """ The server provides Goto Type Definition support. """
    implementationProvider: NotRequired[Union[bool, "ImplementationOptions", "ImplementationRegistrationOptions"]]
    """ The server provides Goto Implementation support. """
    referencesProvider: NotRequired[Union[bool, "ReferenceOptions"]]
    """ The server provides find references support. """
    documentHighlightProvider: NotRequired[Union[bool, "DocumentHighlightOptions"]]
    """ The server provides document highlight support. """
    documentSymbolProvider: NotRequired[Union[bool, "DocumentSymbolOptions"]]
    """ The server provides document symbol support. """
    codeActionProvider: NotRequired[Union[bool, "CodeActionOptions"]]
    """ The server provides code actions. CodeActionOptions may only be
    specified if the client states that it supports
    `codeActionLiteralSupport` in its initial `initialize` request. """
    codeLensProvider: NotRequired["CodeLensOptions"]
    """ The server provides code lens. """
    documentLinkProvider: NotRequired["DocumentLinkOptions"]
    """ The server provides document link support. """
    colorProvider: NotRequired[Union[bool, "DocumentColorOptions", "DocumentColorRegistrationOptions"]]
    """ The server provides color provider support. """
    workspaceSymbolProvider: NotRequired[Union[bool, "WorkspaceSymbolOptions"]]
    """ The server provides workspace symbol support. """
    documentFormattingProvider: NotRequired[Union[bool, "DocumentFormattingOptions"]]
    """ The server provides document formatting. """
    documentRangeFormattingProvider: NotRequired[Union[bool, "DocumentRangeFormattingOptions"]]
    """ The server provides document range formatting. """
    documentOnTypeFormattingProvider: NotRequired["DocumentOnTypeFormattingOptions"]
    """ The server provides document formatting on typing. """
    renameProvider: NotRequired[Union[bool, "RenameOptions"]]
    """ The server provides rename support. RenameOptions may only be
    specified if the client states that it supports
    `prepareSupport` in its initial `initialize` request. """
    foldingRangeProvider: NotRequired[Union[bool, "FoldingRangeOptions", "FoldingRangeRegistrationOptions"]]
    """ The server provides folding provider support. """
    selectionRangeProvider: NotRequired[Union[bool, "SelectionRangeOptions", "SelectionRangeRegistrationOptions"]]
    """ The server provides selection range support. """
    executeCommandProvider: NotRequired["ExecuteCommandOptions"]
    """ The server provides execute command support. """
    callHierarchyProvider: NotRequired[Union[bool, "CallHierarchyOptions", "CallHierarchyRegistrationOptions"]]
    """ The server provides call hierarchy support.

    @since 3.16.0 """
    linkedEditingRangeProvider: NotRequired[Union[bool, "LinkedEditingRangeOptions", "LinkedEditingRangeRegistrationOptions"]]
    """ The server provides linked editing range support.

    @since 3.16.0 """
    semanticTokensProvider: NotRequired[Union["SemanticTokensOptions", "SemanticTokensRegistrationOptions"]]
    """ The server provides semantic tokens support.

    @since 3.16.0 """
    monikerProvider: NotRequired[Union[bool, "MonikerOptions", "MonikerRegistrationOptions"]]
    """ The server provides moniker support.

    @since 3.16.0 """
    typeHierarchyProvider: NotRequired[Union[bool, "TypeHierarchyOptions", "TypeHierarchyRegistrationOptions"]]
    """ The server provides type hierarchy support.

    @since 3.17.0 """
    inlineValueProvider: NotRequired[Union[bool, "InlineValueOptions", "InlineValueRegistrationOptions"]]
    """ The server provides inline values.

    @since 3.17.0 """
    inlayHintProvider: NotRequired[Union[bool, "InlayHintOptions", "InlayHintRegistrationOptions"]]
    """ The server provides inlay hints.

    @since 3.17.0 """
    diagnosticProvider: NotRequired[Union["DiagnosticOptions", "DiagnosticRegistrationOptions"]]
    """ The server has support for pull model diagnostics.

    @since 3.17.0 """
    workspace: NotRequired["__ServerCapabilities_workspace_Type_1"]
    """ Workspace specific server capabilities. """
    experimental: NotRequired["LSPAny"]
    """ Experimental server capabilities. """


class VersionedTextDocumentIdentifier(TypedDict):
    """A text document identifier to denote a specific version of a text document."""

    version: int
    """ The version number of this document. """
    uri: "DocumentUri"
    """ The text document's uri. """


class SaveOptions(TypedDict):
    """Save options."""

    includeText: NotRequired[bool]
    """ The client is supposed to include the content on save. """


class FileEvent(TypedDict):
    """An event describing a file change."""

    uri: "DocumentUri"
    """ The file's uri. """
    type: "FileChangeType"
    """ The change type. """


class FileSystemWatcher(TypedDict):
    globPattern: "GlobPattern"
    """ The glob pattern to watch. See {@link GlobPattern glob pattern} for more detail.

    @since 3.17.0 support for relative patterns. """
    kind: NotRequired["WatchKind"]
    """ The kind of events of interest. If omitted it defaults
    to WatchKind.Create | WatchKind.Change | WatchKind.Delete
    which is 7. """


class Diagnostic(TypedDict):
    """Represents a diagnostic, such as a compiler error or warning. Diagnostic objects
    are only valid in the scope of a resource.
    """

    range: "Range"
    """ The range at which the message applies """
    severity: NotRequired["DiagnosticSeverity"]
    """ The diagnostic's severity. Can be omitted. If omitted it is up to the
    client to interpret diagnostics as error, warning, info or hint. """
    code: NotRequired[int | str]
    """ The diagnostic's code, which usually appear in the user interface. """
    codeDescription: NotRequired["CodeDescription"]
    """ An optional property to describe the error code.
    Requires the code field (above) to be present/not null.

    @since 3.16.0 """
    source: NotRequired[str]
    """ A human-readable string describing the source of this
    diagnostic, e.g. 'typescript' or 'super lint'. It usually
    appears in the user interface. """
    message: str
    """ The diagnostic's message. It usually appears in the user interface """
    tags: NotRequired[list["DiagnosticTag"]]
    """ Additional metadata about the diagnostic.

    @since 3.15.0 """
    relatedInformation: NotRequired[list["DiagnosticRelatedInformation"]]
    """ An array of related diagnostic information, e.g. when symbol-names within
    a scope collide all definitions can be marked via this property. """
    data: NotRequired["LSPAny"]
    """ A data entry field that is preserved between a `textDocument/publishDiagnostics`
    notification and `textDocument/codeAction` request.

    @since 3.16.0 """


class CompletionContext(TypedDict):
    """Contains additional information about the context in which a completion request is triggered."""

    triggerKind: "CompletionTriggerKind"
    """ How the completion was triggered. """
    triggerCharacter: NotRequired[str]
    """ The trigger character (a single character) that has trigger code complete.
    Is undefined if `triggerKind !== CompletionTriggerKind.TriggerCharacter` """


class CompletionItemLabelDetails(TypedDict):
    """Additional details for a completion item label.

    @since 3.17.0
    """

    detail: NotRequired[str]
    """ An optional string which is rendered less prominently directly after {@link CompletionItem.label label},
    without any spacing. Should be used for function signatures and type annotations. """
    description: NotRequired[str]
    """ An optional string which is rendered less prominently after {@link CompletionItem.detail}. Should be used
    for fully qualified names and file paths. """


class InsertReplaceEdit(TypedDict):
    """A special text edit to provide an insert and a replace operation.

    @since 3.16.0
    """

    newText: str
    """ The string to be inserted. """
    insert: "Range"
    """ The range if the insert is requested """
    replace: "Range"
    """ The range if the replace is requested. """


class CompletionOptions(TypedDict):
    """Completion options."""

    triggerCharacters: NotRequired[list[str]]
    """ Most tools trigger completion request automatically without explicitly requesting
    it using a keyboard shortcut (e.g. Ctrl+Space). Typically they do so when the user
    starts to type an identifier. For example if the user types `c` in a JavaScript file
    code complete will automatically pop up present `console` besides others as a
    completion item. Characters that make up identifiers don't need to be listed here.

    If code complete should automatically be trigger on characters not being valid inside
    an identifier (for example `.` in JavaScript) list them in `triggerCharacters`. """
    allCommitCharacters: NotRequired[list[str]]
    """ The list of all possible characters that commit a completion. This field can be used
    if clients don't support individual commit characters per completion item. See
    `ClientCapabilities.textDocument.completion.completionItem.commitCharactersSupport`

    If a server provides both `allCommitCharacters` and commit characters on an individual
    completion item the ones on the completion item win.

    @since 3.2.0 """
    resolveProvider: NotRequired[bool]
    """ The server provides support to resolve additional
    information for a completion item. """
    completionItem: NotRequired["__CompletionOptions_completionItem_Type_2"]
    """ The server supports the following `CompletionItem` specific
    capabilities.

    @since 3.17.0 """
    workDoneProgress: NotRequired[bool]


class HoverOptions(TypedDict):
    """Hover options."""

    workDoneProgress: NotRequired[bool]


class SignatureHelpContext(TypedDict):
    """Additional information about the context in which a signature help request was triggered.

    @since 3.15.0
    """

    triggerKind: "SignatureHelpTriggerKind"
    """ Action that caused signature help to be triggered. """
    triggerCharacter: NotRequired[str]
    """ Character that caused signature help to be triggered.

    This is undefined when `triggerKind !== SignatureHelpTriggerKind.TriggerCharacter` """
    isRetrigger: bool
    """ `true` if signature help was already showing when it was triggered.

    Retriggers occurs when the signature help is already active and can be caused by actions such as
    typing a trigger character, a cursor move, or document content changes. """
    activeSignatureHelp: NotRequired["SignatureHelp"]
    """ The currently active `SignatureHelp`.

    The `activeSignatureHelp` has its `SignatureHelp.activeSignature` field updated based on
    the user navigating through available signatures. """


class SignatureInformation(TypedDict):
    """Represents the signature of something callable. A signature
    can have a label, like a function-name, a doc-comment, and
    a set of parameters.
    """

    label: str
    """ The label of this signature. Will be shown in
    the UI. """
    documentation: NotRequired[Union[str, "MarkupContent"]]
    """ The human-readable doc-comment of this signature. Will be shown
    in the UI but can be omitted. """
    parameters: NotRequired[list["ParameterInformation"]]
    """ The parameters of this signature. """
    activeParameter: NotRequired[Uint]
    """ The index of the active parameter.

    If provided, this is used in place of `SignatureHelp.activeParameter`.

    @since 3.16.0 """


class SignatureHelpOptions(TypedDict):
    """Server Capabilities for a {@link SignatureHelpRequest}."""

    triggerCharacters: NotRequired[list[str]]
    """ List of characters that trigger signature help automatically. """
    retriggerCharacters: NotRequired[list[str]]
    """ List of characters that re-trigger signature help.

    These trigger characters are only active when signature help is already showing. All trigger characters
    are also counted as re-trigger characters.

    @since 3.15.0 """
    workDoneProgress: NotRequired[bool]


class DefinitionOptions(TypedDict):
    """Server Capabilities for a {@link DefinitionRequest}."""

    workDoneProgress: NotRequired[bool]


class ReferenceContext(TypedDict):
    """Value-object that contains additional information when
    requesting references.
    """

    includeDeclaration: bool
    """ Include the declaration of the current symbol. """


class ReferenceOptions(TypedDict):
    """Reference options."""

    workDoneProgress: NotRequired[bool]


class DocumentHighlightOptions(TypedDict):
    """Provider options for a {@link DocumentHighlightRequest}."""

    workDoneProgress: NotRequired[bool]


class BaseSymbolInformation(TypedDict):
    """A base for all symbol information."""

    name: str
    """ The name of this symbol. """
    kind: "SymbolKind"
    """ The kind of this symbol. """
    tags: NotRequired[list["SymbolTag"]]
    """ Tags for this symbol.

    @since 3.16.0 """
    containerName: NotRequired[str]
    """ The name of the symbol containing this symbol. This information is for
    user interface purposes (e.g. to render a qualifier in the user interface
    if necessary). It can't be used to re-infer a hierarchy for the document
    symbols. """


class DocumentSymbolOptions(TypedDict):
    """Provider options for a {@link DocumentSymbolRequest}."""

    label: NotRequired[str]
    """ A human-readable string that is shown when multiple outlines trees
    are shown for the same document.

    @since 3.16.0 """
    workDoneProgress: NotRequired[bool]


class CodeActionContext(TypedDict):
    """Contains additional diagnostic information about the context in which
    a {@link CodeActionProvider.provideCodeActions code action} is run.
    """

    diagnostics: list["Diagnostic"]
    """ An array of diagnostics known on the client side overlapping the range provided to the
    `textDocument/codeAction` request. They are provided so that the server knows which
    errors are currently presented to the user for the given range. There is no guarantee
    that these accurately reflect the error state of the resource. The primary parameter
    to compute code actions is the provided range. """
    only: NotRequired[list["CodeActionKind"]]
    """ Requested kind of actions to return.

    Actions not of this kind are filtered out by the client before being shown. So servers
    can omit computing them. """
    triggerKind: NotRequired["CodeActionTriggerKind"]
    """ The reason why code actions were requested.

    @since 3.17.0 """


class CodeActionOptions(TypedDict):
    """Provider options for a {@link CodeActionRequest}."""

    codeActionKinds: NotRequired[list["CodeActionKind"]]
    """ CodeActionKinds that this server may return.

    The list of kinds may be generic, such as `CodeActionKind.Refactor`, or the server
    may list out every specific kind they provide. """
    resolveProvider: NotRequired[bool]
    """ The server provides support to resolve additional
    information for a code action.

    @since 3.16.0 """
    workDoneProgress: NotRequired[bool]


class WorkspaceSymbolOptions(TypedDict):
    """Server capabilities for a {@link WorkspaceSymbolRequest}."""

    resolveProvider: NotRequired[bool]
    """ The server provides support to resolve additional
    information for a workspace symbol.

    @since 3.17.0 """
    workDoneProgress: NotRequired[bool]


class CodeLensOptions(TypedDict):
    """Code Lens provider options of a {@link CodeLensRequest}."""

    resolveProvider: NotRequired[bool]
    """ Code lens has a resolve provider as well. """
    workDoneProgress: NotRequired[bool]


class DocumentLinkOptions(TypedDict):
    """Provider options for a {@link DocumentLinkRequest}."""

    resolveProvider: NotRequired[bool]
    """ Document links have a resolve provider as well. """
    workDoneProgress: NotRequired[bool]


class FormattingOptions(TypedDict):
    """Value-object describing what options formatting should use."""

    tabSize: Uint
    """ Size of a tab in spaces. """
    insertSpaces: bool
    """ Prefer spaces over tabs. """
    trimTrailingWhitespace: NotRequired[bool]
    """ Trim trailing whitespace on a line.

    @since 3.15.0 """
    insertFinalNewline: NotRequired[bool]
    """ Insert a newline character at the end of the file if one does not exist.

    @since 3.15.0 """
    trimFinalNewlines: NotRequired[bool]
    """ Trim all newlines after the final newline at the end of the file.

    @since 3.15.0 """


class DocumentFormattingOptions(TypedDict):
    """Provider options for a {@link DocumentFormattingRequest}."""

    workDoneProgress: NotRequired[bool]


class DocumentRangeFormattingOptions(TypedDict):
    """Provider options for a {@link DocumentRangeFormattingRequest}."""

    workDoneProgress: NotRequired[bool]


class DocumentOnTypeFormattingOptions(TypedDict):
    """Provider options for a {@link DocumentOnTypeFormattingRequest}."""

    firstTriggerCharacter: str
    """ A character on which formatting should be triggered, like `{`. """
    moreTriggerCharacter: NotRequired[list[str]]
    """ More trigger characters. """


class RenameOptions(TypedDict):
    """Provider options for a {@link RenameRequest}."""

    prepareProvider: NotRequired[bool]
    """ Renames should be checked and tested before being executed.

    @since version 3.12.0 """
    workDoneProgress: NotRequired[bool]


class ExecuteCommandOptions(TypedDict):
    """The server capabilities of a {@link ExecuteCommandRequest}."""

    commands: list[str]
    """ The commands to be executed on the server """
    workDoneProgress: NotRequired[bool]


class SemanticTokensLegend(TypedDict):
    """@since 3.16.0"""

    tokenTypes: list[str]
    """ The token types a server uses. """
    tokenModifiers: list[str]
    """ The token modifiers a server uses. """


class OptionalVersionedTextDocumentIdentifier(TypedDict):
    """A text document identifier to optionally denote a specific version of a text document."""

    version: int | None
    """ The version number of this document. If a versioned text document identifier
    is sent from the server to the client and the file is not open in the editor
    (the server has not received an open notification before) the server can send
    `null` to indicate that the version is unknown and the content on disk is the
    truth (as specified with document content ownership). """
    uri: "DocumentUri"
    """ The text document's uri. """


class AnnotatedTextEdit(TypedDict):
    """A special text edit with an additional change annotation.

    @since 3.16.0.
    """

    annotationId: "ChangeAnnotationIdentifier"
    """ The actual identifier of the change annotation """
    range: "Range"
    """ The range of the text document to be manipulated. To insert
    text into a document create a range where start === end. """
    newText: str
    """ The string to be inserted. For delete operations use an
    empty string. """


class ResourceOperation(TypedDict):
    """A generic resource operation."""

    kind: str
    """ The resource operation kind. """
    annotationId: NotRequired["ChangeAnnotationIdentifier"]
    """ An optional annotation identifier describing the operation.

    @since 3.16.0 """


class CreateFileOptions(TypedDict):
    """Options to create a file."""

    overwrite: NotRequired[bool]
    """ Overwrite existing file. Overwrite wins over `ignoreIfExists` """
    ignoreIfExists: NotRequired[bool]
    """ Ignore if exists. """


class RenameFileOptions(TypedDict):
    """Rename file options"""

    overwrite: NotRequired[bool]
    """ Overwrite target if existing. Overwrite wins over `ignoreIfExists` """
    ignoreIfExists: NotRequired[bool]
    """ Ignores if target exists. """


class DeleteFileOptions(TypedDict):
    """Delete file options"""

    recursive: NotRequired[bool]
    """ Delete the content recursively if a folder is denoted. """
    ignoreIfNotExists: NotRequired[bool]
    """ Ignore the operation if the file doesn't exist. """


class FileOperationPattern(TypedDict):
    """A pattern to describe in which file operation requests or notifications
    the server is interested in receiving.

    @since 3.16.0
    """

    glob: str
    """ The glob pattern to match. Glob patterns can have the following syntax:
    - `*` to match one or more characters in a path segment
    - `?` to match on one character in a path segment
    - `**` to match any number of path segments, including none
    - `{}` to group sub patterns into an OR expression. (e.g. `**\u200b/*.{ts,js}` matches all TypeScript and JavaScript files)
    - `[]` to declare a range of characters to match in a path segment (e.g., `example.[0-9]` to match on `example.0`, `example.1`, …)
    - `[!...]` to negate a range of characters to match in a path segment (e.g., `example.[!0-9]` to match on `example.a`, `example.b`, but not `example.0`) """
    matches: NotRequired["FileOperationPatternKind"]
    """ Whether to match files or folders with this pattern.

    Matches both if undefined. """
    options: NotRequired["FileOperationPatternOptions"]
    """ Additional options used during matching. """


class WorkspaceFullDocumentDiagnosticReport(TypedDict):
    """A full document diagnostic report for a workspace diagnostic result.

    @since 3.17.0
    """

    uri: "DocumentUri"
    """ The URI for which diagnostic information is reported. """
    version: int | None
    """ The version number for which the diagnostics are reported.
    If the document is not marked as open `null` can be provided. """
    kind: Literal["full"]
    """ A full document diagnostic report. """
    resultId: NotRequired[str]
    """ An optional result id. If provided it will
    be sent on the next diagnostic request for the
    same document. """
    items: list["Diagnostic"]
    """ The actual items. """


class WorkspaceUnchangedDocumentDiagnosticReport(TypedDict):
    """An unchanged document diagnostic report for a workspace diagnostic result.

    @since 3.17.0
    """

    uri: "DocumentUri"
    """ The URI for which diagnostic information is reported. """
    version: int | None
    """ The version number for which the diagnostics are reported.
    If the document is not marked as open `null` can be provided. """
    kind: Literal["unchanged"]
    """ A document diagnostic report indicating
    no changes to the last result. A server can
    only return `unchanged` if result ids are
    provided. """
    resultId: str
    """ A result id which will be sent on the next
    diagnostic request for the same document. """


class NotebookCell(TypedDict):
    """A notebook cell.

    A cell's document URI must be unique across ALL notebook
    cells and can therefore be used to uniquely identify a
    notebook cell or the cell's text document.

    @since 3.17.0
    """

    kind: "NotebookCellKind"
    """ The cell's kind """
    document: "DocumentUri"
    """ The URI of the cell's text document
    content. """
    metadata: NotRequired["LSPObject"]
    """ Additional metadata stored with the cell.

    Note: should always be an object literal (e.g. LSPObject) """
    executionSummary: NotRequired["ExecutionSummary"]
    """ Additional execution summary information
    if supported by the client. """


class NotebookCellArrayChange(TypedDict):
    """A change describing how to move a `NotebookCell`
    array from state S to S'.

    @since 3.17.0
    """

    start: Uint
    """ The start oftest of the cell that changed. """
    deleteCount: Uint
    """ The deleted cells """
    cells: NotRequired[list["NotebookCell"]]
    """ The new cells, if any """


class ClientCapabilities(TypedDict):
    """Defines the capabilities provided by the client."""

    workspace: NotRequired["WorkspaceClientCapabilities"]
    """ Workspace specific client capabilities. """
    textDocument: NotRequired["TextDocumentClientCapabilities"]
    """ Text document specific client capabilities. """
    notebookDocument: NotRequired["NotebookDocumentClientCapabilities"]
    """ Capabilities specific to the notebook document support.

    @since 3.17.0 """
    window: NotRequired["WindowClientCapabilities"]
    """ Window specific client capabilities. """
    general: NotRequired["GeneralClientCapabilities"]
    """ General client capabilities.

    @since 3.16.0 """
    experimental: NotRequired["LSPAny"]
    """ Experimental client capabilities. """


class TextDocumentSyncOptions(TypedDict):
    openClose: NotRequired[bool]
    """ Open and close notifications are sent to the server. If omitted open close notification should not
    be sent. """
    change: NotRequired["TextDocumentSyncKind"]
    """ Change notifications are sent to the server. See TextDocumentSyncKind.None, TextDocumentSyncKind.Full
    and TextDocumentSyncKind.Incremental. If omitted it defaults to TextDocumentSyncKind.None. """
    willSave: NotRequired[bool]
    """ If present will save notifications are sent to the server. If omitted the notification should not be
    sent. """
    willSaveWaitUntil: NotRequired[bool]
    """ If present will save wait until requests are sent to the server. If omitted the request should not be
    sent. """
    save: NotRequired[Union[bool, "SaveOptions"]]
    """ If present save notifications are sent to the server. If omitted the notification should not be
    sent. """


class NotebookDocumentSyncOptions(TypedDict):
    """Options specific to a notebook plus its cells
    to be synced to the server.

    If a selector provides a notebook document
    filter but no cell selector all cells of a
    matching notebook document will be synced.

    If a selector provides no notebook document
    filter but only a cell selector all notebook
    document that contain at least one matching
    cell will be synced.

    @since 3.17.0
    """

    notebookSelector: list[
        Union[
            "__NotebookDocumentSyncOptions_notebookSelector_Type_1",
            "__NotebookDocumentSyncOptions_notebookSelector_Type_2",
        ]
    ]
    """ The notebooks to be synced """
    save: NotRequired[bool]
    """ Whether save notification should be forwarded to
    the server. Will only be honored if mode === `notebook`. """



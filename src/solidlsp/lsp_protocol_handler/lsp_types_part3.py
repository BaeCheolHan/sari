from __future__ import annotations

from enum import Enum, IntEnum, IntFlag
from typing import Literal, NotRequired, Union

from typing_extensions import TypedDict

class InlineValueRegistrationOptions(TypedDict):
    """Inline value options used during static or dynamic registration.

    @since 3.17.0
    """

    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """
    id: NotRequired[str]
    """ The id used to register the request. The id can be used to deregister
    the request again. See also Registration#id. """


class InlayHintParams(TypedDict):
    """A parameter literal used in inlay hint requests.

    @since 3.17.0
    """

    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    range: "Range"
    """ The document range for which inlay hints should be computed. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """


class InlayHint(TypedDict):
    """Inlay hint information.

    @since 3.17.0
    """

    position: "Position"
    """ The position of this hint. """
    label: str | list["InlayHintLabelPart"]
    """ The label of this hint. A human readable string or an array of
    InlayHintLabelPart label parts.

    *Note* that neither the string nor the label part can be empty. """
    kind: NotRequired["InlayHintKind"]
    """ The kind of this hint. Can be omitted in which case the client
    should fall back to a reasonable default. """
    textEdits: NotRequired[list["TextEdit"]]
    """ Optional text edits that are performed when accepting this inlay hint.

    *Note* that edits are expected to change the document so that the inlay
    hint (or its nearest variant) is now part of the document and the inlay
    hint itself is now obsolete. """
    tooltip: NotRequired[Union[str, "MarkupContent"]]
    """ The tooltip text when you hover over this item. """
    paddingLeft: NotRequired[bool]
    """ Render padding before the hint.

    Note: Padding should use the editor's background color, not the
    background color of the hint itself. That means padding can be used
    to visually align/separate an inlay hint. """
    paddingRight: NotRequired[bool]
    """ Render padding after the hint.

    Note: Padding should use the editor's background color, not the
    background color of the hint itself. That means padding can be used
    to visually align/separate an inlay hint. """
    data: NotRequired["LSPAny"]
    """ A data entry field that is preserved on an inlay hint between
    a `textDocument/inlayHint` and a `inlayHint/resolve` request. """


class InlayHintRegistrationOptions(TypedDict):
    """Inlay hint options used during static or dynamic registration.

    @since 3.17.0
    """

    resolveProvider: NotRequired[bool]
    """ The server provides support to resolve additional
    information for an inlay hint item. """
    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """
    id: NotRequired[str]
    """ The id used to register the request. The id can be used to deregister
    the request again. See also Registration#id. """


class DocumentDiagnosticParams(TypedDict):
    """Parameters of the document diagnostic request.

    @since 3.17.0
    """

    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    identifier: NotRequired[str]
    """ The additional identifier  provided during registration. """
    previousResultId: NotRequired[str]
    """ The result id of a previous response if provided. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class DocumentDiagnosticReportPartialResult(TypedDict):
    """A partial result for a document diagnostic report.

    @since 3.17.0
    """

    relatedDocuments: dict[
        "DocumentUri",
        Union["FullDocumentDiagnosticReport", "UnchangedDocumentDiagnosticReport"],
    ]


class DiagnosticServerCancellationData(TypedDict):
    """Cancellation data returned from a diagnostic request.

    @since 3.17.0
    """

    retriggerRequest: bool


class DiagnosticRegistrationOptions(TypedDict):
    """Diagnostic registration options.

    @since 3.17.0
    """

    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """
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
    id: NotRequired[str]
    """ The id used to register the request. The id can be used to deregister
    the request again. See also Registration#id. """


class WorkspaceDiagnosticParams(TypedDict):
    """Parameters of the workspace diagnostic request.

    @since 3.17.0
    """

    identifier: NotRequired[str]
    """ The additional identifier provided during registration. """
    previousResultIds: list["PreviousResultId"]
    """ The currently known diagnostic reports with their
    previous result ids. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class WorkspaceDiagnosticReport(TypedDict):
    """A workspace diagnostic report.

    @since 3.17.0
    """

    items: list["WorkspaceDocumentDiagnosticReport"]


class WorkspaceDiagnosticReportPartialResult(TypedDict):
    """A partial result for a workspace diagnostic report.

    @since 3.17.0
    """

    items: list["WorkspaceDocumentDiagnosticReport"]


class DidOpenNotebookDocumentParams(TypedDict):
    """The params sent in an open notebook document notification.

    @since 3.17.0
    """

    notebookDocument: "NotebookDocument"
    """ The notebook document that got opened. """
    cellTextDocuments: list["TextDocumentItem"]
    """ The text documents that represent the content
    of a notebook cell. """


class DidChangeNotebookDocumentParams(TypedDict):
    """The params sent in a change notebook document notification.

    @since 3.17.0
    """

    notebookDocument: "VersionedNotebookDocumentIdentifier"
    """ The notebook document that did change. The version number points
    to the version after all provided changes have been applied. If
    only the text document content of a cell changes the notebook version
    doesn't necessarily have to change. """
    change: "NotebookDocumentChangeEvent"
    """ The actual changes to the notebook document.

    The changes describe single state changes to the notebook document.
    So if there are two changes c1 (at array index 0) and c2 (at array
    index 1) for a notebook in state S then c1 moves the notebook from
    S to S' and c2 from S' to S''. So c1 is computed on the state S and
    c2 is computed on the state S'.

    To mirror the content of a notebook using change events use the following approach:
    - start with the same initial content
    - apply the 'notebookDocument/didChange' notifications in the order you receive them.
    - apply the `NotebookChangeEvent`s in a single notification in the order
      you receive them. """


class DidSaveNotebookDocumentParams(TypedDict):
    """The params sent in a save notebook document notification.

    @since 3.17.0
    """

    notebookDocument: "NotebookDocumentIdentifier"
    """ The notebook document that got saved. """


class DidCloseNotebookDocumentParams(TypedDict):
    """The params sent in a close notebook document notification.

    @since 3.17.0
    """

    notebookDocument: "NotebookDocumentIdentifier"
    """ The notebook document that got closed. """
    cellTextDocuments: list["TextDocumentIdentifier"]
    """ The text documents that represent the content
    of a notebook cell that got closed. """


class RegistrationParams(TypedDict):
    registrations: list["Registration"]


class UnregistrationParams(TypedDict):
    unregisterations: list["Unregistration"]


class InitializeParams(TypedDict):
    processId: int | None
    """ The process Id of the parent process that started
    the server.

    Is `null` if the process has not been started by another process.
    If the parent process is not alive then the server should exit. """
    clientInfo: NotRequired["___InitializeParams_clientInfo_Type_1"]
    """ Information about the client

    @since 3.15.0 """
    locale: NotRequired[str]
    """ The locale the client is currently showing the user interface
    in. This must not necessarily be the locale of the operating
    system.

    Uses IETF language tags as the value's syntax
    (See https://en.wikipedia.org/wiki/IETF_language_tag)

    @since 3.16.0 """
    rootPath: NotRequired[str | None]
    """ The rootPath of the workspace. Is null
    if no folder is open.

    @deprecated in favour of rootUri. """
    rootUri: Union["DocumentUri", None]
    """ The rootUri of the workspace. Is null if no
    folder is open. If both `rootPath` and `rootUri` are set
    `rootUri` wins.

    @deprecated in favour of workspaceFolders. """
    capabilities: "ClientCapabilities"
    """ The capabilities provided by the client (editor or tool) """
    initializationOptions: NotRequired["LSPAny"]
    """ User provided initialization options. """
    trace: NotRequired["TraceValues"]
    """ The initial trace setting. If omitted trace is disabled ('off'). """
    workspaceFolders: NotRequired[list["WorkspaceFolder"] | None]
    """ The workspace folders configured in the client when the server starts.

    This property is only available if the client supports workspace folders.
    It can be `null` if the client supports workspace folders but none are
    configured.

    @since 3.6.0 """


class InitializeResult(TypedDict):
    """The result returned from an initialize request."""

    capabilities: "ServerCapabilities"
    """ The capabilities the language server provides. """
    serverInfo: NotRequired["__InitializeResult_serverInfo_Type_1"]
    """ Information about the server.

    @since 3.15.0 """


class InitializeError(TypedDict):
    """The data type of the ResponseError if the
    initialize request fails.
    """

    retry: bool
    """ Indicates whether the client execute the following retry logic:
    (1) show the message provided by the ResponseError to the user
    (2) user selects retry or cancel
    (3) if user selected retry the initialize method is sent again. """


class InitializedParams(TypedDict):
    ...


class DidChangeConfigurationParams(TypedDict):
    """The parameters of a change configuration notification."""

    settings: "LSPAny"
    """ The actual changed settings """


class DidChangeConfigurationRegistrationOptions(TypedDict):
    section: NotRequired[str | list[str]]


class ShowMessageParams(TypedDict):
    """The parameters of a notification message."""

    type: "MessageType"
    """ The message type. See {@link MessageType} """
    message: str
    """ The actual message. """


class ShowMessageRequestParams(TypedDict):
    type: "MessageType"
    """ The message type. See {@link MessageType} """
    message: str
    """ The actual message. """
    actions: NotRequired[list["MessageActionItem"]]
    """ The message action items to present. """


class MessageActionItem(TypedDict):
    title: str
    """ A short title like 'Retry', 'Open Log' etc. """


class LogMessageParams(TypedDict):
    """The log message parameters."""

    type: "MessageType"
    """ The message type. See {@link MessageType} """
    message: str
    """ The actual message. """


class DidOpenTextDocumentParams(TypedDict):
    """The parameters sent in an open text document notification"""

    textDocument: "TextDocumentItem"
    """ The document that was opened. """


class DidChangeTextDocumentParams(TypedDict):
    """The change text document notification's parameters."""

    textDocument: "VersionedTextDocumentIdentifier"
    """ The document that did change. The version number points
    to the version after all provided content changes have
    been applied. """
    contentChanges: list["TextDocumentContentChangeEvent"]
    """ The actual content changes. The content changes describe single state changes
    to the document. So if there are two content changes c1 (at array index 0) and
    c2 (at array index 1) for a document in state S then c1 moves the document from
    S to S' and c2 from S' to S''. So c1 is computed on the state S and c2 is computed
    on the state S'.

    To mirror the content of a document using change events use the following approach:
    - start with the same initial content
    - apply the 'textDocument/didChange' notifications in the order you receive them.
    - apply the `TextDocumentContentChangeEvent`s in a single notification in the order
      you receive them. """


class TextDocumentChangeRegistrationOptions(TypedDict):
    """Describe options to be used when registered for text document change events."""

    syncKind: "TextDocumentSyncKind"
    """ How documents are synced to the server. """
    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """


class DidCloseTextDocumentParams(TypedDict):
    """The parameters sent in a close text document notification"""

    textDocument: "TextDocumentIdentifier"
    """ The document that was closed. """


class DidSaveTextDocumentParams(TypedDict):
    """The parameters sent in a save text document notification"""

    textDocument: "TextDocumentIdentifier"
    """ The document that was saved. """
    text: NotRequired[str]
    """ Optional the content when saved. Depends on the includeText value
    when the save notification was requested. """


class TextDocumentSaveRegistrationOptions(TypedDict):
    """Save registration options."""

    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """
    includeText: NotRequired[bool]
    """ The client is supposed to include the content on save. """


class WillSaveTextDocumentParams(TypedDict):
    """The parameters sent in a will save text document notification."""

    textDocument: "TextDocumentIdentifier"
    """ The document that will be saved. """
    reason: "TextDocumentSaveReason"
    """ The 'TextDocumentSaveReason'. """


class TextEdit(TypedDict):
    """A text edit applicable to a text document."""

    range: "Range"
    """ The range of the text document to be manipulated. To insert
    text into a document create a range where start === end. """
    newText: str
    """ The string to be inserted. For delete operations use an
    empty string. """


class DidChangeWatchedFilesParams(TypedDict):
    """The watched files change notification's parameters."""

    changes: list["FileEvent"]
    """ The actual file events. """


class DidChangeWatchedFilesRegistrationOptions(TypedDict):
    """Describe options to be used when registered for text document change events."""

    watchers: list["FileSystemWatcher"]
    """ The watchers to register. """


class PublishDiagnosticsParams(TypedDict):
    """The publish diagnostic notification's parameters."""

    uri: "DocumentUri"
    """ The URI for which diagnostic information is reported. """
    version: NotRequired[int]
    """ Optional the version number of the document the diagnostics are published for.

    @since 3.15.0 """
    diagnostics: list["Diagnostic"]
    """ An array of diagnostic information items. """


class CompletionParams(TypedDict):
    """Completion parameters"""

    context: NotRequired["CompletionContext"]
    """ The completion context. This is only available it the client specifies
    to send this using the client capability `textDocument.completion.contextSupport === true` """
    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    position: "Position"
    """ The position inside the text document. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class CompletionItem(TypedDict):
    """A completion item represents a text snippet that is
    proposed to complete text that is being typed.
    """

    label: str
    """ The label of this completion item.

    The label property is also by default the text that
    is inserted when selecting this completion.

    If label details are provided the label itself should
    be an unqualified name of the completion item. """
    labelDetails: NotRequired["CompletionItemLabelDetails"]
    """ Additional details for the label

    @since 3.17.0 """
    kind: NotRequired["CompletionItemKind"]
    """ The kind of this completion item. Based of the kind
    an icon is chosen by the editor. """
    tags: NotRequired[list["CompletionItemTag"]]
    """ Tags for this completion item.

    @since 3.15.0 """
    detail: NotRequired[str]
    """ A human-readable string with additional information
    about this item, like type or symbol information. """
    documentation: NotRequired[Union[str, "MarkupContent"]]
    """ A human-readable string that represents a doc-comment. """
    deprecated: NotRequired[bool]
    """ Indicates if this item is deprecated.
    @deprecated Use `tags` instead. """
    preselect: NotRequired[bool]
    """ Select this item when showing.

    *Note* that only one completion item can be selected and that the
    tool / client decides which item that is. The rule is that the *first*
    item of those that match best is selected. """
    sortText: NotRequired[str]
    """ A string that should be used when comparing this item
    with other items. When `falsy` the {@link CompletionItem.label label}
    is used. """
    filterText: NotRequired[str]
    """ A string that should be used when filtering a set of
    completion items. When `falsy` the {@link CompletionItem.label label}
    is used. """
    insertText: NotRequired[str]
    """ A string that should be inserted into a document when selecting
    this completion. When `falsy` the {@link CompletionItem.label label}
    is used.

    The `insertText` is subject to interpretation by the client side.
    Some tools might not take the string literally. For example
    VS Code when code complete is requested in this example
    `con<cursor position>` and a completion item with an `insertText` of
    `console` is provided it will only insert `sole`. Therefore it is
    recommended to use `textEdit` instead since it avoids additional client
    side interpretation. """
    insertTextFormat: NotRequired["InsertTextFormat"]
    """ The format of the insert text. The format applies to both the
    `insertText` property and the `newText` property of a provided
    `textEdit`. If omitted defaults to `InsertTextFormat.PlainText`.

    Please note that the insertTextFormat doesn't apply to
    `additionalTextEdits`. """
    insertTextMode: NotRequired["InsertTextMode"]
    """ How whitespace and indentation is handled during completion
    item insertion. If not provided the clients default value depends on
    the `textDocument.completion.insertTextMode` client capability.

    @since 3.16.0 """
    textEdit: NotRequired[Union["TextEdit", "InsertReplaceEdit"]]
    """ An {@link TextEdit edit} which is applied to a document when selecting
    this completion. When an edit is provided the value of
    {@link CompletionItem.insertText insertText} is ignored.

    Most editors support two different operations when accepting a completion
    item. One is to insert a completion text and the other is to replace an
    existing text with a completion text. Since this can usually not be
    predetermined by a server it can report both ranges. Clients need to
    signal support for `InsertReplaceEdits` via the
    `textDocument.completion.insertReplaceSupport` client capability
    property.

    *Note 1:* The text edit's range as well as both ranges from an insert
    replace edit must be a [single line] and they must contain the position
    at which completion has been requested.
    *Note 2:* If an `InsertReplaceEdit` is returned the edit's insert range
    must be a prefix of the edit's replace range, that means it must be
    contained and starting at the same position.

    @since 3.16.0 additional type `InsertReplaceEdit` """
    textEditText: NotRequired[str]
    """ The edit text used if the completion item is part of a CompletionList and
    CompletionList defines an item default for the text edit range.

    Clients will only honor this property if they opt into completion list
    item defaults using the capability `completionList.itemDefaults`.

    If not provided and a list's default range is provided the label
    property is used as a text.

    @since 3.17.0 """
    additionalTextEdits: NotRequired[list["TextEdit"]]
    """ An optional array of additional {@link TextEdit text edits} that are applied when
    selecting this completion. Edits must not overlap (including the same insert position)
    with the main {@link CompletionItem.textEdit edit} nor with themselves.

    Additional text edits should be used to change text unrelated to the current cursor position
    (for example adding an import statement at the top of the file if the completion item will
    insert an unqualified type). """
    commitCharacters: NotRequired[list[str]]
    """ An optional set of characters that when pressed while this completion is active will accept it first and
    then type that character. *Note* that all commit characters should have `length=1` and that superfluous
    characters will be ignored. """
    command: NotRequired["Command"]
    """ An optional {@link Command command} that is executed *after* inserting this completion. *Note* that
    additional modifications to the current document should be described with the
    {@link CompletionItem.additionalTextEdits additionalTextEdits}-property. """
    data: NotRequired["LSPAny"]
    """ A data entry field that is preserved on a completion item between a
    {@link CompletionRequest} and a {@link CompletionResolveRequest}. """


class CompletionList(TypedDict):
    """Represents a collection of {@link CompletionItem completion items} to be presented
    in the editor.
    """

    isIncomplete: bool
    """ This list it not complete. Further typing results in recomputing this list.

    Recomputed lists have all their items replaced (not appended) in the
    incomplete completion sessions. """
    itemDefaults: NotRequired["__CompletionList_itemDefaults_Type_1"]
    """ In many cases the items of an actual completion result share the same
    value for properties like `commitCharacters` or the range of a text
    edit. A completion list can therefore define item defaults which will
    be used if a completion item itself doesn't specify the value.

    If a completion list specifies a default value and a completion item
    also specifies a corresponding value the one from the item is used.

    Servers are only allowed to return default values if the client
    signals support for this via the `completionList.itemDefaults`
    capability.

    @since 3.17.0 """
    items: list["CompletionItem"]
    """ The completion items. """


class CompletionRegistrationOptions(TypedDict):
    """Registration options for a {@link CompletionRequest}."""

    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """
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
    completionItem: NotRequired["__CompletionOptions_completionItem_Type_1"]
    """ The server supports the following `CompletionItem` specific
    capabilities.

    @since 3.17.0 """


class HoverParams(TypedDict):
    """Parameters for a {@link HoverRequest}."""

    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    position: "Position"
    """ The position inside the text document. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """


class Hover(TypedDict):
    """The result of a hover request."""

    contents: Union["MarkupContent", "MarkedString", list["MarkedString"]]
    """ The hover's content """
    range: NotRequired["Range"]
    """ An optional range inside the text document that is used to
    visualize the hover, e.g. by changing the background color. """


class HoverRegistrationOptions(TypedDict):
    """Registration options for a {@link HoverRequest}."""

    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """


class SignatureHelpParams(TypedDict):
    """Parameters for a {@link SignatureHelpRequest}."""

    context: NotRequired["SignatureHelpContext"]
    """ The signature help context. This is only available if the client specifies
    to send this using the client capability `textDocument.signatureHelp.contextSupport === true`

    @since 3.15.0 """
    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    position: "Position"
    """ The position inside the text document. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """


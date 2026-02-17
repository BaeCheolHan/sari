from __future__ import annotations

from enum import Enum, IntEnum, IntFlag
from typing import Literal, NotRequired, Union

from typing_extensions import TypedDict

class SignatureHelp(TypedDict):
    """Signature help represents the signature of something
    callable. There can be multiple signature but only one
    active and only one active parameter.
    """

    signatures: list["SignatureInformation"]
    """ One or more signatures. """
    activeSignature: NotRequired[Uint]
    """ The active signature. If omitted or the value lies outside the
    range of `signatures` the value defaults to zero or is ignored if
    the `SignatureHelp` has no signatures.

    Whenever possible implementers should make an active decision about
    the active signature and shouldn't rely on a default value.

    In future version of the protocol this property might become
    mandatory to better express this. """
    activeParameter: NotRequired[Uint]
    """ The active parameter of the active signature. If omitted or the value
    lies outside the range of `signatures[activeSignature].parameters`
    defaults to 0 if the active signature has parameters. If
    the active signature has no parameters it is ignored.
    In future version of the protocol this property might become
    mandatory to better express the active parameter if the
    active signature does have any. """


class SignatureHelpRegistrationOptions(TypedDict):
    """Registration options for a {@link SignatureHelpRequest}."""

    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """
    triggerCharacters: NotRequired[list[str]]
    """ List of characters that trigger signature help automatically. """
    retriggerCharacters: NotRequired[list[str]]
    """ List of characters that re-trigger signature help.

    These trigger characters are only active when signature help is already showing. All trigger characters
    are also counted as re-trigger characters.

    @since 3.15.0 """


class DefinitionParams(TypedDict):
    """Parameters for a {@link DefinitionRequest}."""

    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    position: "Position"
    """ The position inside the text document. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class DefinitionRegistrationOptions(TypedDict):
    """Registration options for a {@link DefinitionRequest}."""

    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """


class ReferenceParams(TypedDict):
    """Parameters for a {@link ReferencesRequest}."""

    context: "ReferenceContext"
    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    position: "Position"
    """ The position inside the text document. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class ReferenceRegistrationOptions(TypedDict):
    """Registration options for a {@link ReferencesRequest}."""

    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """


class DocumentHighlightParams(TypedDict):
    """Parameters for a {@link DocumentHighlightRequest}."""

    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    position: "Position"
    """ The position inside the text document. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class DocumentHighlight(TypedDict):
    """A document highlight is a range inside a text document which deserves
    special attention. Usually a document highlight is visualized by changing
    the background color of its range.
    """

    range: "Range"
    """ The range this highlight applies to. """
    kind: NotRequired["DocumentHighlightKind"]
    """ The highlight kind, default is {@link DocumentHighlightKind.Text text}. """


class DocumentHighlightRegistrationOptions(TypedDict):
    """Registration options for a {@link DocumentHighlightRequest}."""

    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """


class DocumentSymbolParams(TypedDict):
    """Parameters for a {@link DocumentSymbolRequest}."""

    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class SymbolInformation(TypedDict):
    """Represents information about programming constructs like variables, classes,
    interfaces etc.
    """

    deprecated: NotRequired[bool]
    """ Indicates if this symbol is deprecated.

    @deprecated Use tags instead """
    location: "Location"
    """ The location of this symbol. The location's range is used by a tool
    to reveal the location in the editor. If the symbol is selected in the
    tool the range's start information is used to position the cursor. So
    the range usually spans more than the actual symbol's name and does
    normally include things like visibility modifiers.

    The range doesn't have to denote a node range in the sense of an abstract
    syntax tree. It can therefore not be used to re-construct a hierarchy of
    the symbols. """
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


class DocumentSymbol(TypedDict):
    """Represents programming constructs like variables, classes, interfaces etc.
    that appear in a document. Document symbols can be hierarchical and they
    have two ranges: one that encloses its definition and one that points to
    its most interesting range, e.g. the range of an identifier.
    """

    name: str
    """ The name of this symbol. Will be displayed in the user interface and therefore must not be
    an empty string or a string only consisting of white spaces. """
    detail: NotRequired[str]
    """ More detail for this symbol, e.g the signature of a function. """
    kind: "SymbolKind"
    """ The kind of this symbol. """
    tags: NotRequired[list["SymbolTag"]]
    """ Tags for this document symbol.

    @since 3.16.0 """
    deprecated: NotRequired[bool]
    """ Indicates if this symbol is deprecated.

    @deprecated Use tags instead """
    range: "Range"
    """ The range enclosing this symbol not including leading/trailing whitespace but everything else
    like comments. This information is typically used to determine if the clients cursor is
    inside the symbol to reveal in the symbol in the UI. """
    selectionRange: "Range"
    """ The range that should be selected and revealed when this symbol is being picked, e.g the name of a function.
    Must be contained by the `range`. """

    # NOTE: 원문 스펙 대비 children 필드 누락 가능성에 대한 메모


class DocumentSymbolRegistrationOptions(TypedDict):
    """Registration options for a {@link DocumentSymbolRequest}."""

    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """
    label: NotRequired[str]
    """ A human-readable string that is shown when multiple outlines trees
    are shown for the same document.

    @since 3.16.0 """


class CodeActionParams(TypedDict):
    """The parameters of a {@link CodeActionRequest}."""

    textDocument: "TextDocumentIdentifier"
    """ The document in which the command was invoked. """
    range: "Range"
    """ The range for which the command was invoked. """
    context: "CodeActionContext"
    """ Context carrying additional information. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class Command(TypedDict):
    """Represents a reference to a command. Provides a title which
    will be used to represent a command in the UI and, optionally,
    an array of arguments which will be passed to the command handler
    function when invoked.
    """

    title: str
    """ Title of the command, like `save`. """
    command: str
    """ The identifier of the actual command handler. """
    arguments: NotRequired[list["LSPAny"]]
    """ Arguments that the command handler should be
    invoked with. """


class CodeAction(TypedDict):
    """A code action represents a change that can be performed in code, e.g. to fix a problem or
    to refactor code.

    A CodeAction must set either `edit` and/or a `command`. If both are supplied, the `edit` is applied first, then the `command` is executed.
    """

    title: str
    """ A short, human-readable, title for this code action. """
    kind: NotRequired["CodeActionKind"]
    """ The kind of the code action.

    Used to filter code actions. """
    diagnostics: NotRequired[list["Diagnostic"]]
    """ The diagnostics that this code action resolves. """
    isPreferred: NotRequired[bool]
    """ Marks this as a preferred action. Preferred actions are used by the `auto fix` command and can be targeted
    by keybindings.

    A quick fix should be marked preferred if it properly addresses the underlying error.
    A refactoring should be marked preferred if it is the most reasonable choice of actions to take.

    @since 3.15.0 """
    disabled: NotRequired["__CodeAction_disabled_Type_1"]
    """ Marks that the code action cannot currently be applied.

    Clients should follow the following guidelines regarding disabled code actions:

      - Disabled code actions are not shown in automatic [lightbulbs](https://code.visualstudio.com/docs/editor/editingevolved#_code-action)
        code action menus.

      - Disabled actions are shown as faded out in the code action menu when the user requests a more specific type
        of code action, such as refactorings.

      - If the user has a [keybinding](https://code.visualstudio.com/docs/editor/refactoring#_keybindings-for-code-actions)
        that auto applies a code action and only disabled code actions are returned, the client should show the user an
        error message with `reason` in the editor.

    @since 3.16.0 """
    edit: NotRequired["WorkspaceEdit"]
    """ The workspace edit this code action performs. """
    command: NotRequired["Command"]
    """ A command this code action executes. If a code action
    provides an edit and a command, first the edit is
    executed and then the command. """
    data: NotRequired["LSPAny"]
    """ A data entry field that is preserved on a code action between
    a `textDocument/codeAction` and a `codeAction/resolve` request.

    @since 3.16.0 """


class CodeActionRegistrationOptions(TypedDict):
    """Registration options for a {@link CodeActionRequest}."""

    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """
    codeActionKinds: NotRequired[list["CodeActionKind"]]
    """ CodeActionKinds that this server may return.

    The list of kinds may be generic, such as `CodeActionKind.Refactor`, or the server
    may list out every specific kind they provide. """
    resolveProvider: NotRequired[bool]
    """ The server provides support to resolve additional
    information for a code action.

    @since 3.16.0 """


class WorkspaceSymbolParams(TypedDict):
    """The parameters of a {@link WorkspaceSymbolRequest}."""

    query: str
    """ A query string to filter symbols by. Clients may send an empty
    string here to request all symbols. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class WorkspaceSymbol(TypedDict):
    """A special workspace symbol that supports locations without a range.

    See also SymbolInformation.

    @since 3.17.0
    """

    location: Union["Location", "__WorkspaceSymbol_location_Type_1"]
    """ The location of the symbol. Whether a server is allowed to
    return a location without a range depends on the client
    capability `workspace.symbol.resolveSupport`.

    See SymbolInformation#location for more details. """
    data: NotRequired["LSPAny"]
    """ A data entry field that is preserved on a workspace symbol between a
    workspace symbol request and a workspace symbol resolve request. """
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


class WorkspaceSymbolRegistrationOptions(TypedDict):
    """Registration options for a {@link WorkspaceSymbolRequest}."""

    resolveProvider: NotRequired[bool]
    """ The server provides support to resolve additional
    information for a workspace symbol.

    @since 3.17.0 """


class CodeLensParams(TypedDict):
    """The parameters of a {@link CodeLensRequest}."""

    textDocument: "TextDocumentIdentifier"
    """ The document to request code lens for. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class CodeLens(TypedDict):
    """A code lens represents a {@link Command command} that should be shown along with
    source text, like the number of references, a way to run tests, etc.

    A code lens is _unresolved_ when no command is associated to it. For performance
    reasons the creation of a code lens and resolving should be done in two stages.
    """

    range: "Range"
    """ The range in which this code lens is valid. Should only span a single line. """
    command: NotRequired["Command"]
    """ The command this code lens represents. """
    data: NotRequired["LSPAny"]
    """ A data entry field that is preserved on a code lens item between
    a {@link CodeLensRequest} and a [CodeLensResolveRequest]
    (#CodeLensResolveRequest) """


class CodeLensRegistrationOptions(TypedDict):
    """Registration options for a {@link CodeLensRequest}."""

    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """
    resolveProvider: NotRequired[bool]
    """ Code lens has a resolve provider as well. """


class DocumentLinkParams(TypedDict):
    """The parameters of a {@link DocumentLinkRequest}."""

    textDocument: "TextDocumentIdentifier"
    """ The document to provide document links for. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class DocumentLink(TypedDict):
    """A document link is a range in a text document that links to an internal or external resource, like another
    text document or a web site.
    """

    range: "Range"
    """ The range this link applies to. """
    target: NotRequired[str]
    """ The uri this link points to. If missing a resolve request is sent later. """
    tooltip: NotRequired[str]
    """ The tooltip text when you hover over this link.

    If a tooltip is provided, is will be displayed in a string that includes instructions on how to
    trigger the link, such as `{0} (ctrl + click)`. The specific instructions vary depending on OS,
    user settings, and localization.

    @since 3.15.0 """
    data: NotRequired["LSPAny"]
    """ A data entry field that is preserved on a document link between a
    DocumentLinkRequest and a DocumentLinkResolveRequest. """


class DocumentLinkRegistrationOptions(TypedDict):
    """Registration options for a {@link DocumentLinkRequest}."""

    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """
    resolveProvider: NotRequired[bool]
    """ Document links have a resolve provider as well. """


class DocumentFormattingParams(TypedDict):
    """The parameters of a {@link DocumentFormattingRequest}."""

    textDocument: "TextDocumentIdentifier"
    """ The document to format. """
    options: "FormattingOptions"
    """ The format options. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """


class DocumentFormattingRegistrationOptions(TypedDict):
    """Registration options for a {@link DocumentFormattingRequest}."""

    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """


class DocumentRangeFormattingParams(TypedDict):
    """The parameters of a {@link DocumentRangeFormattingRequest}."""

    textDocument: "TextDocumentIdentifier"
    """ The document to format. """
    range: "Range"
    """ The range to format """
    options: "FormattingOptions"
    """ The format options """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """


class DocumentRangeFormattingRegistrationOptions(TypedDict):
    """Registration options for a {@link DocumentRangeFormattingRequest}."""

    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """


class DocumentOnTypeFormattingParams(TypedDict):
    """The parameters of a {@link DocumentOnTypeFormattingRequest}."""

    textDocument: "TextDocumentIdentifier"
    """ The document to format. """
    position: "Position"
    """ The position around which the on type formatting should happen.
    This is not necessarily the exact position where the character denoted
    by the property `ch` got typed. """
    ch: str
    """ The character that has been typed that triggered the formatting
    on type request. That is not necessarily the last character that
    got inserted into the document since the client could auto insert
    characters as well (e.g. like automatic brace completion). """
    options: "FormattingOptions"
    """ The formatting options. """


class DocumentOnTypeFormattingRegistrationOptions(TypedDict):
    """Registration options for a {@link DocumentOnTypeFormattingRequest}."""

    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """
    firstTriggerCharacter: str
    """ A character on which formatting should be triggered, like `{`. """
    moreTriggerCharacter: NotRequired[list[str]]
    """ More trigger characters. """


class RenameParams(TypedDict):
    """The parameters of a {@link RenameRequest}."""

    textDocument: "TextDocumentIdentifier"
    """ The document to rename. """
    position: "Position"
    """ The position at which this request was sent. """
    newName: str
    """ The new name of the symbol. If the given name is not valid the
    request must return a {@link ResponseError} with an
    appropriate message set. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """


class RenameRegistrationOptions(TypedDict):
    """Registration options for a {@link RenameRequest}."""

    documentSelector: Union["DocumentSelector", None]
    """ A document selector to identify the scope of the registration. If set to null
    the document selector provided on the client side will be used. """
    prepareProvider: NotRequired[bool]
    """ Renames should be checked and tested before being executed.

    @since version 3.12.0 """


class PrepareRenameParams(TypedDict):
    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    position: "Position"
    """ The position inside the text document. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """


class ExecuteCommandParams(TypedDict):
    """The parameters of a {@link ExecuteCommandRequest}."""

    command: str
    """ The identifier of the actual command handler. """
    arguments: NotRequired[list["LSPAny"]]
    """ Arguments that the command should be invoked with. """
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """


class ExecuteCommandRegistrationOptions(TypedDict):
    """Registration options for a {@link ExecuteCommandRequest}."""

    commands: list[str]
    """ The commands to be executed on the server """


class ApplyWorkspaceEditParams(TypedDict):
    """The parameters passed via a apply workspace edit request."""

    label: NotRequired[str]
    """ An optional label of the workspace edit. This label is
    presented in the user interface for example on an undo
    stack to undo the workspace edit. """
    edit: "WorkspaceEdit"
    """ The edits to apply. """


class ApplyWorkspaceEditResult(TypedDict):
    """The result returned from the apply workspace edit request.

    @since 3.17 renamed from ApplyWorkspaceEditResponse
    """

    applied: bool
    """ Indicates whether the edit was applied or not. """
    failureReason: NotRequired[str]
    """ An optional textual description for why the edit was not applied.
    This may be used by the server for diagnostic logging or to provide
    a suitable error for a request that triggered the edit. """
    failedChange: NotRequired[Uint]
    """ Depending on the client's failure handling strategy `failedChange` might
    contain the index of the change that failed. This property is only available
    if the client signals a `failureHandlingStrategy` in its client capabilities. """


class WorkDoneProgressBegin(TypedDict):
    kind: Literal["begin"]
    title: str
    """ Mandatory title of the progress operation. Used to briefly inform about
    the kind of operation being performed.

    Examples: "Indexing" or "Linking dependencies". """
    cancellable: NotRequired[bool]
    """ Controls if a cancel button should show to allow the user to cancel the
    long running operation. Clients that don't support cancellation are allowed
    to ignore the setting. """
    message: NotRequired[str]
    """ Optional, more detailed associated progress message. Contains
    complementary information to the `title`.

    Examples: "3/25 files", "project/src/module2", "node_modules/some_dep".
    If unset, the previous progress message (if any) is still valid. """
    percentage: NotRequired[Uint]
    """ Optional progress percentage to display (value 100 is considered 100%).
    If not provided infinite progress is assumed and clients are allowed
    to ignore the `percentage` value in subsequent in report notifications.

    The value should be steadily rising. Clients are free to ignore values
    that are not following this rule. The value range is [0, 100]. """


class WorkDoneProgressReport(TypedDict):
    kind: Literal["report"]
    cancellable: NotRequired[bool]
    """ Controls enablement state of a cancel button.

    Clients that don't support cancellation or don't support controlling the button's
    enablement state are allowed to ignore the property. """
    message: NotRequired[str]
    """ Optional, more detailed associated progress message. Contains
    complementary information to the `title`.

    Examples: "3/25 files", "project/src/module2", "node_modules/some_dep".
    If unset, the previous progress message (if any) is still valid. """
    percentage: NotRequired[Uint]
    """ Optional progress percentage to display (value 100 is considered 100%).
    If not provided infinite progress is assumed and clients are allowed
    to ignore the `percentage` value in subsequent in report notifications.

    The value should be steadily rising. Clients are free to ignore values
    that are not following this rule. The value range is [0, 100] """


class WorkDoneProgressEnd(TypedDict):
    kind: Literal["end"]
    message: NotRequired[str]
    """ Optional, a final message indicating to for example indicate the outcome
    of the operation. """


class SetTraceParams(TypedDict):
    value: "TraceValues"


class LogTraceParams(TypedDict):
    message: str
    verbose: NotRequired[str]


class CancelParams(TypedDict):
    id: int | str
    """ The request id to cancel. """


class ProgressParams(TypedDict):
    token: "ProgressToken"
    """ The progress token provided by the client or server. """
    value: "LSPAny"
    """ The progress data. """


class TextDocumentPositionParams(TypedDict):
    """A parameter literal used in requests to pass a text document and a position inside that
    document.
    """

    textDocument: "TextDocumentIdentifier"
    """ The text document. """
    position: "Position"
    """ The position inside the text document. """


class WorkDoneProgressParams(TypedDict):
    workDoneToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report work done progress. """


class PartialResultParams(TypedDict):
    partialResultToken: NotRequired["ProgressToken"]
    """ An optional token that a server can use to report partial results (e.g. streaming) to
    the client. """


class LocationLink(TypedDict):
    """Represents the connection of two locations. Provides additional metadata over normal {@link Location locations},
    including an origin range.
    """

    originSelectionRange: NotRequired["Range"]
    """ Span of the origin of this link.

    Used as the underlined span for mouse interaction. Defaults to the word range at
    the definition position. """
    targetUri: "DocumentUri"
    """ The target resource identifier of this link. """
    targetRange: "Range"
    """ The full target range of this link. If the target for example is a symbol then target range is the
    range enclosing this symbol not including leading/trailing whitespace but everything else
    like comments. This information is typically used to highlight the range in the editor. """
    targetSelectionRange: "Range"
    """ The range that should be selected and revealed when this link is being followed, e.g the name of a function.
    Must be contained by the `targetRange`. See also `DocumentSymbol#range` """


class Range(TypedDict):
    """A range in a text document expressed as (zero-based) start and end positions.

    If you want to specify a range that contains a line including the line ending
    character(s) then use an end position denoting the start of the next line.
    For example:
    ```ts
    {
        start: { line: 5, character: 23 }
        end : { line 6, character : 0 }
    }
    ```
    """

    start: "Position"
    """ The range's start position. """
    end: "Position"
    """ The range's end position. """


class ImplementationOptions(TypedDict):
    workDoneProgress: NotRequired[bool]



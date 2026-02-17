from __future__ import annotations

from enum import Enum, IntEnum, IntFlag
from typing import Literal, NotRequired, Union

from typing_extensions import TypedDict

class __WorkspaceSymbolClientCapabilities_tagSupport_Type_1(TypedDict):
    valueSet: list["SymbolTag"]
    """ The tags supported by the client. """


class __WorkspaceSymbol_location_Type_1(TypedDict):
    uri: "DocumentUri"


class ___InitializeParams_clientInfo_Type_1(TypedDict):
    name: str
    """ The name of the client as defined by the client. """
    version: NotRequired[str]
    """ The client's version as defined by the client. """

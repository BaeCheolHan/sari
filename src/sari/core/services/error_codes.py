from enum import Enum


class ServiceErrorCode(str, Enum):
    INTERNAL = "INTERNAL"
    ERR_INDEXER_FOLLOWER = "ERR_INDEXER_FOLLOWER"
    ERR_INDEXER_DISABLED = "ERR_INDEXER_DISABLED"


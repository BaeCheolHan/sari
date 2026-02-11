import pytest
from unittest.mock import MagicMock

from sari.core.services.call_graph.service import CallGraphService


def test_call_graph_service_rejects_non_object_args():
    svc = CallGraphService(MagicMock(), ["/tmp/ws"])
    with pytest.raises(ValueError, match="args must be an object"):
        svc.build(["bad-args"])

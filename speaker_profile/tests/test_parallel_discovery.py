"""Profile adapter integration uses synthetic sources and the unchanged validator."""
import threading

from knowledge import parallel_discovery
from .test_corpus_discovery import cfg, root, ready, no_provider, discovery, pin, SyntheticDiscovery


def test_parallel_profile_preserves_compact_inputs_and_serial_validation(ready):
    barrier = threading.Barrier(2, timeout=10)

    class Model(SyntheticDiscovery):
        def complete(self, *args):
            barrier.wait()
            return super().complete(*args)

    before = discovery._implementation()
    result = parallel_discovery.execute(discovery, pin(ready["path"]), command="run",
                                        workers=2, backend_factory=Model)
    assert result["status"] == "completed"
    assert result["model_calls_this_execution"] == 2
    assert discovery._implementation() == before
    assert discovery.execute(pin(ready["path"]), command="validate")["status"] == "validated"
    assert parallel_discovery.execute(discovery, pin(ready["path"]), command="run", workers=2,
                                      backend_factory=Model)["model_calls_this_execution"] == 0

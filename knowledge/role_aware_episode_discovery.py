"""Knowledge episode successor accepting distinct roles within one family.

NEW config/run only: knowledge_role_aware_episode_discovery_config.v1,
membership_policy=unique_family_role.v1. Keep the original episode packet strategy.
No old receipts are adopted implicitly; recovery requires explicit pinned provenance.
"""
from pathlib import Path
import sys

from transcription.common import sha256_file
from . import episode_discovery as base, role_aware_discovery as shared

ADAPTER_NAME = "knowledge.role_aware_episode_discovery"
CONFIG_VERSION = "knowledge_role_aware_episode_discovery_config.v1"
RUN_VERSION = "knowledge_role_aware_episode_discovery_run.v1"
REQUEST_VERSION = "knowledge_role_aware_episode_discovery_request.v1"
RESULT_VERSION = base.RESULT_VERSION
CONFIG = shared.configuration(base.CONFIG, CONFIG_VERSION)
PROPOSAL, POLICY, PROMPT_PATH = base.PROPOSAL, base.POLICY, base.PROMPT_PATH
_size, _materialize, _aggregate, _counts = base._size, base._materialize, base._aggregate, base._counts
_check_pins = base._check_pins


def _implementation():
    return {"role_adapter_sha256": sha256_file(Path(__file__)),
            "role_validator_sha256": sha256_file(Path(shared.__file__)),
            "membership_policy": shared.MEMBERSHIP_POLICY, "episode": base._implementation()}


def _prepare(config_ref, progress=None):
    return shared.prepare(sys.modules[__name__], base, config_ref, progress)


def _derive(response, context):
    return shared.derive(response, context, proposal=PROPOSAL, domain="knowledge")


def execute(config_ref, **kwargs):
    return shared.execute(sys.modules[__name__], config_ref, **kwargs)


def main(argv=None):
    return shared.main(sys.modules[__name__], argv)


if __name__ == "__main__":
    raise SystemExit(main())

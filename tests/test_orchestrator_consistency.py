"""Cross-file consistency between domains.py / tools_registry.py / agent.py -
no DB/LLM needed, pure structural checks. This exact class of drift has
bitten this project twice already: LIFE_FRAGMENT telling the model to call
a tool "life" didn't actually register, and _CLAIM_STEM_TOOLS["отправил"]
pointing at a tool (send_email) that was deleted from TOOLS entirely. Both
would have been caught immediately by the checks below.
"""
from app.orchestrator import agent, domains, tools_registry


def test_every_domain_tool_name_is_registered():
    for module in domains.DOMAIN_MODULES:
        missing = module.tool_names - tools_registry.TOOLS.keys()
        assert not missing, f'domain "{module.name}" references unregistered tools: {missing}'


def test_claim_stem_tools_are_all_registered():
    for stem, tools in agent._CLAIM_STEM_TOOLS.items():
        missing = tools - tools_registry.TOOLS.keys()
        assert not missing, f"stem {stem!r} in _CLAIM_STEM_TOOLS expects unregistered tools: {missing}"


def test_every_claim_stem_matches_the_confirmation_pattern():
    # A stem present in _CLAIM_STEM_TOOLS but not in
    # _CONFIRMATION_STEM_PATTERN can never actually be looked up (finditer
    # would never produce a match starting with it) - dead entry, or a typo
    # in one of the two lists.
    for stem in agent._CLAIM_STEM_TOOLS:
        assert agent._CONFIRMATION_STEM_PATTERN.search(stem), (
            f"stem {stem!r} in _CLAIM_STEM_TOOLS doesn't match _CONFIRMATION_STEM_PATTERN itself"
        )

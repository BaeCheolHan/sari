from sari.core.scoring import ScoringPolicy


def test_scoring_policy_tolerates_non_mapping_config():
    policy = ScoringPolicy(config=["bad-config"])

    boost = policy.get_symbol_boost("class", is_exact=True)

    assert isinstance(boost, float)
    assert boost > 0

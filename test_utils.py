from data_handlers import _clean_repo_name
from state_manager import _fresh_state

def test_clean_repo_name():
    assert _clean_repo_name("https://github.com/aref98/nodejs-rest-api") == "aref98/nodejs-rest-api"
    assert _clean_repo_name("owner/repo.git") == "owner/repo"
    assert _clean_repo_name("owner/repo") == "owner/repo"
    assert _clean_repo_name("invalid_string") is None

def test_fresh_state():
    # Test that the state manager initializes correctly
    state = _fresh_state("2026-01-01", "2026-01-07", skip_phase_a=True, total_shards=5)
    assert state["status"] == "in_progress"
    assert state["total_shards"] == 5
    assert state["skip_phase_a"] is True
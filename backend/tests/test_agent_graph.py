import pytest

from scripts.agent_cases import CASES, run_case


@pytest.mark.parametrize('case_id', CASES)
def test_real_graph_resume_mcp_and_verified_outcome(case_id):
    result = run_case(case_id)
    assert result['status'] == CASES[case_id]['terminal']
    assert not result['errors']

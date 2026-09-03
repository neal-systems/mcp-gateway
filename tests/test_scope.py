import pytest

import scope


def test_empty_operator_list_fails_closed():
    with pytest.raises(scope.ConfigError):
        scope.load_allowlist({"GATEWAY_OPERATOR_GITHUB_IDS": ""})


def test_placeholder_id_is_rejected():
    with pytest.raises(scope.ConfigError):
        scope.load_allowlist({"GATEWAY_OPERATOR_GITHUB_IDS": "YOUR_GITHUB_ID"})


def test_id_in_both_roles_is_rejected():
    with pytest.raises(scope.ConfigError):
        scope.load_allowlist(
            {
                "GATEWAY_OPERATOR_GITHUB_IDS": "111111111",
                "GATEWAY_VIEWER_GITHUB_IDS": "111111111",
            }
        )


def test_allowlist_assigns_roles():
    result = scope.load_allowlist(
        {
            "GATEWAY_OPERATOR_GITHUB_IDS": "111111111",
            "GATEWAY_VIEWER_GITHUB_IDS": "222222222,333333333",
        }
    )
    assert result == {
        "111111111": scope.ROLE_OPERATOR,
        "222222222": scope.ROLE_VIEWER,
        "333333333": scope.ROLE_VIEWER,
    }


def test_unknown_identity_receives_no_role():
    allowlist = {"111111111": scope.ROLE_OPERATOR}
    assert scope.role_for("999999999", allowlist) is None
    assert scope.role_for(None, allowlist) is None


def test_unknown_role_receives_empty_catalog():
    assert scope.allowed_tools(None) == frozenset()
    assert scope.allowed_tools("unexpected") == frozenset()


def test_viewer_catalog_is_narrower_than_operator_catalog():
    operator = scope.allowed_tools(scope.ROLE_OPERATOR)
    viewer = scope.allowed_tools(scope.ROLE_VIEWER)
    assert viewer < operator
    assert "search_runbooks" not in viewer

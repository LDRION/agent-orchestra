"""Движок решений: сведение ролей, наследование по дереву, запрет по умолчанию."""
from orchestra.iam.authz import AccessContext, OrgTree
from orchestra.iam.models import User

TREE = OrgTree(parent={"co": None, "arch": "co", "bim": "co", "proj": "arch"},
               names={"co": "Компания", "arch": "Архитектура", "bim": "BIM", "proj": "Проект"})


def ctx(memberships, grants, owner=False, active=True) -> AccessContext:
    user = User(id="u1", login="u", display_name="U", is_owner=owner, is_active=active)
    return AccessContext(user=user, tree=TREE, memberships=memberships, grants=grants,
                         role_names={"emp": "Сотрудник", "head": "Руководитель", "ban": "Запрет"})


def test_default_deny():
    assert ctx([], {}).decide("agents.edit").effect == "deny"


def test_unknown_permission_denied_even_for_owner():
    assert ctx([], {}, owner=True).decide("agents.fly").effect == "deny"


def test_owner_allowed_but_inactive_denied():
    assert ctx([], {}, owner=True).decide("iam.roles").allowed
    assert ctx([], {}, owner=True, active=False).decide("iam.roles").effect == "deny"


def test_role_inherits_down_the_tree_not_up():
    c = ctx([("arch", "head")], {"head": {"agents.edit": ("allow", None)}})
    assert c.decide("agents.edit", "proj").allowed            # вложенный проект
    assert c.decide("agents.edit", "arch").allowed
    assert c.decide("agents.edit", "bim").effect == "deny"     # соседний отдел
    assert c.decide("agents.edit", "co").effect == "deny"      # родитель


def test_deny_beats_allow():
    c = ctx([("co", "head"), ("arch", "ban")],
            {"head": {"tools.web": ("allow", None)}, "ban": {"tools.web": ("deny", None)}})
    assert c.decide("tools.web", "bim").allowed
    d = c.decide("tools.web", "proj")
    assert d.effect == "deny" and "Запрет" in d.reason


def test_allow_beats_approval_and_approval_carries_approvers():
    c = ctx([("co", "emp")], {"emp": {"providers.external": ("approval", "head")}})
    d = c.decide("providers.external", "arch")
    assert d.needs_approval and d.approver_role_ids == ["head"]
    c2 = ctx([("co", "emp"), ("arch", "head")], {"emp": {"providers.external": ("approval", None)},
                                                 "head": {"providers.external": ("allow", None)}})
    assert c2.decide("providers.external", "proj").allowed


def test_units_with_respects_deny():
    c = ctx([("co", "head"), ("arch", "ban")],
            {"head": {"runs.view_all": ("allow", None)}, "ban": {"runs.view_all": ("deny", None)}})
    assert c.units_with("runs.view_all") == {"co", "bim"}


def test_tree_chain_survives_cycle():
    t = OrgTree(parent={"a": "b", "b": "a"}, names={})
    assert set(t.chain("a")) == {"a", "b"}

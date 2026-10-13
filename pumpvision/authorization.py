"""Central role-to-capability policy.

Existing role decorators deliberately remain exact-role checks: a dashboard view
capability must never imply permission to run that dashboard's actions.  New
cross-role experiences can depend on these additive view capabilities without
changing the authorization contract of any existing route.
"""
from collections.abc import Mapping


VIEW_ADMIN_DASHBOARD = "dashboard.admin.view"
VIEW_MANAGER_DASHBOARD = "dashboard.manager.view"
VIEW_ATTENDANT_DASHBOARD = "dashboard.attendant.view"
VIEW_DEVELOPER_DASHBOARD = "dashboard.developer.view"
VIEW_OPS_DASHBOARD = "dashboard.ops.view"

DASHBOARD_VIEW_CAPABILITIES = frozenset({
    VIEW_ADMIN_DASHBOARD,
    VIEW_MANAGER_DASHBOARD,
    VIEW_ATTENDANT_DASHBOARD,
    VIEW_DEVELOPER_DASHBOARD,
})

# Keep this policy explicit.  In particular, super_admin receives no action or
# write capability.  A future audited action mode should introduce separate
# capabilities rather than broadening any of the view capabilities below.
ROLE_CAPABILITIES: Mapping[str, frozenset[str]] = {
    "owner": frozenset({VIEW_ADMIN_DASHBOARD, VIEW_OPS_DASHBOARD}),
    "manager": frozenset({VIEW_MANAGER_DASHBOARD, VIEW_OPS_DASHBOARD}),
    "attendant": frozenset({VIEW_ATTENDANT_DASHBOARD}),
    "developer": frozenset({VIEW_DEVELOPER_DASHBOARD, VIEW_OPS_DASHBOARD}),
    "super_admin": DASHBOARD_VIEW_CAPABILITIES | frozenset({VIEW_OPS_DASHBOARD}),
}


def capabilities_for_role(role: str | None) -> frozenset[str]:
    """Return an immutable capability set; unknown roles are denied by default."""
    return ROLE_CAPABILITIES.get(role or "", frozenset())


def has_capability(user, capability: str) -> bool:
    """Return whether an authenticated user has one explicitly granted capability."""
    return bool(
        getattr(user, "is_authenticated", False)
        and capability in capabilities_for_role(getattr(user, "role", None))
    )

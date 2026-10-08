# Super Admin console

PumpVision supports a distinct `super_admin` user role. It lands at
`/super-admin/` and provides a unified, responsive console with Overview,
Admin, Manager, Attendant, and Developer tabs.

## Authorization model

`pumpvision/authorization.py` is the central additive capability policy. The
dashboard view capabilities are:

- `dashboard.admin.view`
- `dashboard.manager.view`
- `dashboard.attendant.view`
- `dashboard.developer.view`

Owner, manager, attendant, and developer retain only their corresponding view
capability and all existing role behavior. `super_admin` receives all four
view capabilities and no write/action capability. Unknown roles receive no
capabilities.

Existing operational routes continue to use exact-role decorators. A view
capability does not grant access to an original dashboard route or to its GET
or POST actions. The Super Admin console has separate GET-only preview routes,
and its templates contain no forms or mutation links.

## Read-only boundary

The console reuses the read-only data builders behind the existing dashboards,
but renders dedicated preview templates:

- Admin shows completed-shift accounting, fuel, reconciliation, and stock data.
- Manager shows task and pending-verification status without logging controls.
- Attendant shows shift status; sale, meter, test, and submit controls are unavailable.
- Developer shows health and findings; run-review and acknowledge controls are unavailable.
- Overview shows safe component health and open operational/reconciliation finding
  titles. It does not expose credentials, environment variables, or secrets.

Each tab is a separate route. Only the selected tab builds and queries its
dashboard data. Responses use `Cache-Control: no-store`.

No impersonation is implemented. A `super_admin` account must be provisioned
through the normal controlled user-management/database process; source code and
bootstrap logic do not create one or set a password. The existing `users.role`
string column already accommodates the value, so no migration is required.

## Future audited action mode

Do not broaden a `dashboard.*.view` capability to add actions. A future write
mode should introduce narrowly scoped action capabilities, explicit server-side
decorators, step-up authentication, and an immutable audit record containing
actor, action, target, timestamp, and outcome. That work is intentionally not
part of this feature; MFA and write mode remain out of scope.

## Verification

Run the isolated suite with:

```bash
python3 -m unittest discover -v
```

The tests cover the capability matrix, console isolation, all preview tabs,
representative write endpoint denials, role login redirects, unchanged dashboard
rendering for existing roles, duplicate HTML IDs, and absence of forms in preview
templates.

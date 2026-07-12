Phase 3A: Add household budgeting and payday-based cash-flow forecasting to LedgerLens.

Review the current main branch before making changes.

Primary objective:
Help the user understand what is due before the next payday and the lowest
projected available balance during that period.

Do not add Azure deployment, authentication, external banking APIs or
generative AI during this phase.

1. Enforce read-only GET routes
- Audit every GET route.
- Move PayPal-description backfills and import-metadata repairs out of GET routes.
- Implement explicit Flask CLI commands or deliberate POST maintenance actions.
- Add tests with data that would actually trigger each maintenance operation.
- Prove that all GET requests leave persisted data unchanged.

2. Add database migrations
- Introduce Flask-Migrate and Alembic.
- Create an initial migration representing the current schema.
- Document:
  flask --app run.py db upgrade
- Do not delete or recreate the development database automatically.

3. Add CSRF protection
- Use Flask-WTF or an equivalent established Flask solution.
- Protect every state-changing POST form.
- Update tests to obtain or disable CSRF only through the test configuration.
- Keep CSRF enabled by default outside tests.

4. Create forecast models
Add focused models such as:

IncomeSchedule:
- generic display name
- account
- amount
- frequency: weekly, fortnightly, monthly, irregular
- next_expected_date
- active

PlannedCommitment:
- generic display name
- category
- household flag
- amount
- frequency
- next_expected_date
- optional end date
- active
- commitment type: bill, allowance, groceries, pet, transport, savings, other

OneOffForecastEvent:
- display name
- amount
- event date
- income or expense
- category
- household flag
- status: planned, completed, cancelled

Do not place personal names, employers or real account information in source
code, migrations, tests or seed data.

5. Build a forecast service
Create a dedicated cashflow_forecast_service.py.

Given:
- opening balance
- forecast start date
- forecast end date
- active income schedules
- confirmed recurring bills
- planned commitments
- one-off forecast events

Return:
- ordered forecast events
- total expected income
- total expected expenditure
- projected closing balance
- minimum projected balance
- date of minimum projected balance
- commitments due before next income
- warnings for missing or stale data

Use Decimal exclusively for money.

6. Keep forecasts separate from actual transactions
- Forecast events must not create Transaction rows.
- Matching an imported actual transaction to a forecast should be a future,
  explicit reconciliation action.
- Clearly label every value as Actual, Forecast or Estimated.

7. Add payday forecast page
Create a page showing:
- opening balance
- next expected payday
- days until payday
- chronological income and expense events
- running projected balance
- minimum projected balance
- projected balance immediately before payday
- data-completeness warning

Allow:
- next-payday view
- next 30 days
- next 90 days
- custom date range

8. Add planned household commitments
Provide create, edit, deactivate and delete workflows.

Support:
- weekly
- fortnightly
- monthly
- quarterly
- annual
- one-off

Examples in tests and placeholders must remain generic:
- Example Allowance
- Example Pet Food
- Example Grocery Budget
- Example Annual Charge

9. Add sinking-fund provisions
Allow annual or irregular costs to have:
- target amount
- due date
- amount already reserved
- recommended amount per payday
- linked savings goal if appropriate

Display the recommendation as a calculation, not definitive financial advice.

10. Improve service boundaries
- Move recurring-bill deactivation logic out of route handlers and into
  recurrence_service.py.
- Avoid route-local imports unless required to resolve a documented circular
  dependency.
- Keep route handlers focused on validation, service calls and rendering.

11. Testing
Add tests for:
- GET routes with backfillable data remain read-only
- fortnightly income schedules
- weekly and fortnightly commitments
- annual commitment projections
- chronological running balances
- minimum balance occurring before closing date
- one-off expenses
- inactive schedules excluded
- forecast rows do not create Transaction records
- Decimal calculations
- stale-data warnings
- CSRF protection
- migration upgrade from an empty test database

Run:
pytest -q

Confirm:
python run.py

12. Git workflow
Use incremental commits such as:
- "Move maintenance writes out of GET routes"
- "Add migrations and CSRF protection"
- "Add forecast schedule models"
- "Add payday cash flow forecast"
- "Add planned commitments and sinking funds"

Push only after tests pass.

Existing constraints remain:
- no personal or real financial information in code or documentation;
- secrets in .env;
- local data excluded by .gitignore;
- Jinja template inheritance;
- styles in app/static/css/styles.css;
- scripts in app/static/js/scripts.js;
- Tailwind styling;
- no inline CSS or JavaScript.
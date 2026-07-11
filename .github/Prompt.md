Phase 2B: Build trusted, reviewable financial intelligence for LedgerLens.

Review the current repository at the latest main branch before changing code.

Primary objective:
Convert automatic merchant, recurrence, cash-flow and savings calculations into
reviewable, explainable workflows. Do not add cloud hosting, Azure SQL,
authentication, external APIs or generative AI.

1. Prevent GET requests from mutating data
- Audit all GET routes.
- The /intelligence GET request must not create, update or commit RecurringBill records.
- Recurrence detection should return candidate suggestions without persisting them.
- Persist only after an explicit POST action by the user.
- Add regression tests proving GET requests are read-only.

2. Refactor financial_intelligence.py
Split responsibilities into focused services where practical:
- merchant_service.py
- recurrence_service.py
- cashflow_service.py
- household_analytics.py
- savings_service.py

Keep route handlers thin.
Do not duplicate business logic.
Retain helpful docstrings and function-purpose comments.

3. Add recurring-payment candidates
Create a persistent RecurringCandidate model or an equivalent clean workflow.

Each candidate should include:
- merchant or normalised description
- observed transaction count
- first and last observed dates
- median or typical amount
- amount variation
- estimated frequency
- estimated next date
- confidence score
- status: pending, confirmed, rejected
- created_at and reviewed_at

Do not automatically promote a candidate into a confirmed recurring bill.

4. Support these recurrence frequencies
- weekly
- fortnightly
- monthly
- quarterly
- annual
- irregular

Allow a reasonable date tolerance.
Use Decimal for amount calculations.
Do not assume that every recurring payment has a fixed amount.

5. Add recurring-candidate review UI
Create a review page showing the evidence behind each candidate.

Actions:
- Confirm
- Reject
- Edit and confirm

The edit form should allow:
- display name
- category
- frequency
- expected amount
- amount tolerance
- expected next date
- household flag
- active/inactive

6. Improve merchant mapping management
Add a page or section that can:
- list merchant aliases and target merchants
- edit an alias
- disable or delete an incorrect mapping
- preview how many transactions a mapping would affect
- apply changes only after explicit confirmation

Track mapping origin:
- manual
- imported
- inferred

7. Parse money safely
Do not parse financial input through float.

Create and test a shared parse_money helper that:
- accepts a string
- returns a Decimal quantized to two decimal places
- rejects blank, invalid, NaN and infinite values
- applies appropriate non-negative validation

Update savings and other money forms to use it.

8. Expand savings recovery into an event history
Add a SavingsRecoveryEvent or equivalent model.

Event types:
- withdrawal
- repayment
- adjustment

Fields:
- savings goal
- event date
- amount
- event type
- generic reason/category
- optional note
- created_at

Calculate the current recovery position from events rather than only overwriting
a current balance.

Display:
- original target
- current balance
- total emergency withdrawals
- total repaid
- amount still to recover
- progress percentage
- event history

Allow an optional repayment amount per payday and estimate the number of paydays
needed to recover.

9. Add data-completeness reporting
Show:
- latest imported transaction date for each account
- number of unreviewed transactions
- number of uncategorised transactions
- accounts with no recent import
- date ranges represented in the dashboard
- a visible warning when analysis is incomplete

Do not present incomplete information as definitive financial advice.

10. Add reporting period controls
Support:
- current month
- previous month
- last 3 months
- custom date range

Use a shared period-filter service so dashboard totals and analytics use the same
date boundaries.

11. Testing
Add focused tests for:
- GET routes do not alter database rows
- weekly recurrence
- fortnightly recurrence
- monthly recurrence
- annual recurrence
- variable amounts within tolerance
- false recurring candidates
- candidate confirm/reject/edit
- merchant mapping preview and explicit application
- Decimal money parsing
- savings withdrawal and repayment history
- current recovery calculations
- data-completeness warnings
- period filtering

Run:
pytest -q

Confirm:
python run.py

12. Git workflow
Make small incremental commits, for example:
- "Refactor financial intelligence services"
- "Add recurring candidate review workflow"
- "Add savings recovery event history"
- "Add data completeness and period filters"

Push to origin/main only after tests pass.

Constraints:
- No real names, emails, account numbers, merchant records or financial data.
- Use generic test fixtures and placeholders.
- Keep secrets in .env.
- Keep local databases, uploads and statement files untracked.
- Preserve Jinja template inheritance.
- Keep styles in app/static/css/styles.css.
- Keep scripts in app/static/js/scripts.js.
- Use Tailwind for styling.
- Do not use inline CSS or inline JavaScript.
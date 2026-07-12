# LedgerLens Project Handover Snapshot

Last updated: 12 July 2026

## 1. Project Purpose

LedgerLens is a local-first Flask application for reviewing household financial data, separating actual transactions from estimates, and planning cash requirements before upcoming payments.

The application currently provides deterministic observations, forecasts, and household planning guidance. It does not provide regulated financial advice, investment recommendations, or generative-AI advice.

Privacy remains a core constraint:

- Real statements, databases, uploads, secrets, and environment files must remain outside Git.
- Development prompts under `.github/Prompt*.md` are ignored.
- Tests and documentation use generic examples.
- Local account balances, allocations, and reconciliation decisions remain in the local database.

## 2. Current Technology

- Python and Flask
- Flask-SQLAlchemy
- Flask-Migrate and Alembic
- Flask-WTF CSRF protection
- SQLite for local development
- Jinja template inheritance
- Tailwind utility classes plus `app/static/css/styles.css`
- Vanilla JavaScript in `app/static/js/scripts.js`
- pytest

## 3. Architecture

```text
Import
  ↓
Normalise
  ↓
Review
  ↓
Enrich
  ↓
Analyse
  ↓
Forecast and household planning
```

The main application areas are:

- `app/models/`: persistent ledger, review, forecast, allocation, and reconciliation data.
- `app/routes/main.py`: dashboard, accounts, imports, reviews, mappings, recurrence, and recovery.
- `app/routes/forecast.py`: payday forecast, daily health, budgets, income allocation, and reconciliation.
- `app/services/`: focused financial calculations and import-processing rules.
- `app/templates/`: accessible server-rendered interfaces.
- `migrations/versions/`: non-destructive schema history.
- `tests/`: application, route, service, workflow, migration, and security coverage.

## 4. Import and Transaction Review

Implemented capabilities include:

- Generic CSV imports with explicit destination-account selection.
- Dedicated PayPal and Credit Union import handling.
- Statement fingerprints and import metadata.
- Cross-batch duplicate detection and non-destructive exclusion.
- Original descriptions retained for audit.
- Review states, categories, household flags, notes, exclusions, and internal-transfer markers.
- Stable description-pattern learning for changing payment references.
- Explicit maintenance actions and CLI commands; GET requests remain read-only.

Excluded or internal transactions are preserved in the ledger rather than deleted.

## 5. Merchant and Categorisation Controls

- Merchant aliases have an origin, active state, category, and household assignment.
- Mapping impact can be previewed before application.
- Mapping application requires an explicit POST action.
- Aliases can be edited, disabled, or deleted.
- Historical transactions are not silently changed during page display.

## 6. Recurring Payments

- Detection supports weekly, fortnightly, monthly, quarterly, annual, and irregular patterns.
- Detection excludes internal transfers, excluded rows, and savings-tracking accounts.
- Candidates retain observation dates, typical amount, variation, estimated next date, and confidence evidence.
- Suggestions remain pending until explicitly confirmed.
- Confirmed rules can be edited, rejected, or deactivated without changing their source transactions.
- Missing-payment reports use confirmed active rules only.

## 7. Savings Recovery

- Recovery uses append-only withdrawal, repayment, and adjustment events.
- Current recovery position is calculated from the baseline plus event history.
- Generic reason categories are enforced.
- Recovery summaries include withdrawn, repaid, outstanding, progress, and estimated paydays.

## 8. Payday Forecasting

- Actual transactions remain separate from forecast schedules.
- Supported inputs include income schedules, confirmed recurring bills, planned commitments, one-off events, variable budgets, and sinking-fund provisions.
- Forecasts provide chronological events, closing position, minimum position, and commitments before income.
- Income schedules preserve total expected pay for reporting.
- Only explicit household-contribution allocations improve household available cash.

## 9. Income Allocation

Income allocation separates:

- Total expected income.
- Household contributions.
- Personal allocations.
- Savings allocations.
- Unknown or unallocated income.

Allocations support fixed amounts or percentages, destination accounts, effective dates, status, source, and availability classification.

Important rule:

> Total income may be reported, but only money explicitly allocated to the household budget may improve the household financial-health forecast.

An income schedule and its contribution may have different timing. Ad hoc contributions create no assumed future top-up. Incoming destination-account transactions are shown as candidates and require explicit confirmation as actual contributions.

## 10. Daily Financial Health

The Daily Health page is organised around a household decision:

> How much needs to be contributed, and by what date, to keep upcoming payments covered?

The primary plan displays:

- Current joint-account balance.
- Available funds, including any overdraft.
- Payments before the next expected household income.
- Suggested contribution to avoid using the overdraft.
- Minimum shortfall that would exceed all available funds.
- Contribution deadline.
- Lowest projected balance after the suggested contribution.

Detailed income, allocation, spending, forecast, confidence, and reconciliation figures remain available in expandable sections.

Health states are explicit and evidence-based:

- `healthy`
- `caution`
- `at_risk`
- `critical`
- `insufficient_data`

## 11. Balance Snapshots and Overdrafts

Accounts support:

- A bank-provided current-balance snapshot.
- Balance as-of date.
- Overdraft limit.
- Reporting scope.

Forecasts use the snapshot plus later eligible transactions instead of reconstructing the current balance from incomplete history.

Overdraft capacity is shown as available credit, not income. The application distinguishes the contribution needed to avoid overdraft usage from the amount required to prevent a payment exceeding all available funds.

## 12. Account Reporting Scopes

Supported scopes are:

- `household_operating`
- `personal`
- `savings_tracking`

Savings-tracking accounts are excluded from household income, spending, cash-flow, and recurring-payment analysis. They retain a complete ledger and appear in a separate Savings Health summary.

Credit Union payroll entries are treated as internal Savings movements. Savings summaries use the absolute statement amount as the amount saved while preserving the original imported amount and description.

## 13. Data Completeness and Confidence

Completeness reporting includes:

- Earliest and latest account activity.
- Stale account warnings.
- Possible date gaps.
- Pending and uncategorised transactions.
- Excluded rows.
- Partial reporting-period coverage.

Forecast confidence can be high, moderate, low, or insufficient. Reasons include stale destination-account data, incomplete reviews, missing allocations, estimated contributions, overdue contributions, and estimated non-visible household spending.

## 14. Reporting Periods

Shared inclusive periods support:

- Current month.
- Previous month.
- Last three months.
- Year to date.
- Custom range.

Dashboard, cash-flow, household analytics, and completeness calculations use shared period boundaries.

## 15. Security and Auditability

- CSRF protection is enabled by default for state-changing forms.
- Tests disable CSRF only through test configuration unless specifically testing CSRF behavior.
- GET routes do not create, update, delete, backfill, or commit records.
- Financial input is parsed with `Decimal`; NaN, infinity, invalid, and disallowed negative values are rejected.
- Forecast calculations do not create actual `Transaction` records.
- User review is required for low-confidence matches and reconciliation decisions.

## 16. Database Migrations

Current migration head:

```text
9f2a6b3c8d41
```

The local development database is currently at that head.

Apply migrations with:

```bash
flask --app run.py db upgrade
```

Do not delete or recreate the local database to apply schema changes.

## 17. Verification Snapshot

Latest full-suite result:

```text
108 passed
```

Known non-application warning:

- pytest reports a Windows cache-directory warning because an existing `.pytest_cache` path cannot be recreated. Tests still complete successfully.

Verified pages return HTTP 200 against the migrated local database:

- Dashboard
- Accounts
- Forecast
- Daily Health
- Income Allocation

## 18. Git State

The working tree was clean when this snapshot was prepared.

Recent local commits not yet pushed at the time of this snapshot:

```text
37d6a26 Separate savings accounts from household cash flow
e757f47 Use account balance snapshots in household planning
7170b3f Simplify the household contribution plan
8b3bdcb Support ad hoc household contributions
```

These commits contain generic implementation and tests only. Local account values and identifiers are not committed.

## 19. Local-Only Configuration

The local database currently contains user-reviewed configuration for:

- Household and personal account reporting roles.
- A separate savings-tracking account.
- Current balance snapshots and overdraft settings.
- An ad hoc contribution-only income allocation.
- Confirmed recurring commitments and variable planning data.
- Internal savings and transfer classifications.

Exact names, identifiers, balances, and amounts are intentionally omitted from this document.

## 20. Current Limitations

- No authentication or multi-household support.
- No cloud deployment.
- No external banking APIs or automatic balance refresh.
- No regulated financial advice.
- No generative-AI recommendations.
- Contribution and payment matches require review.
- Forecast accuracy depends on recent balance snapshots, imports, allocations, and commitment maintenance.
- Possible date gaps are warnings rather than proof that a statement is missing.

## 21. Recommended Next Actions

1. Review unmatched incoming transfers and confirm only genuine household contributions.
2. Review overdue or unmatched expected payments so the suggested contribution is based on current evidence.
3. Keep household balance snapshots and overdraft limits current.
4. Keep savings-tracking statement imports current and review payroll-savings totals.
5. Review variable household budgets and essential-payment flags.
6. Run the full suite before further commits:

   ```bash
   pytest -q
   ```

7. Start the application and confirm the main planning pages:

   ```bash
   python run.py
   ```

8. Push the outstanding local commits only after explicit authorization and a final privacy review.

## 22. Handover Principle

Preserve these boundaries in future work:

- Actual transactions are evidence.
- Forecasts and budgets are estimates.
- Total income is not automatically available household cash.
- Overdraft is credit, not income.
- Savings accounts are not household operating accounts.
- Reconciliation requires explicit review.
- Incomplete data must be labelled as incomplete.

# LedgerLens

LedgerLens is a private, local-first household finance dashboard built with Flask. It helps you import CSV transactions, normalize data, detect duplicates, map merchants, and review records before categorization.

This project is designed for local development with placeholder sample data only.

## Privacy Warning

Do not commit real financial data, exported bank files, personal details, secrets, or local database files to version control.

## Tech Stack

- Python
- Flask + Jinja2
- Tailwind CSS (CDN)
- Vanilla JavaScript
- SQLite (local development)
- pytest

## Local Setup

1. Create and activate a virtual environment:

```bash
python -m venv .venv
.venv\Scripts\activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Create your environment file from the example:

```bash
copy .env.example .env
```

4. Apply database migrations:

```bash
flask --app run.py db upgrade
```

5. Start the app:

```bash
python run.py
```

The app will be available at `http://127.0.0.1:5000`.

## Environment Configuration

Use `.env` for local configuration:

- `SECRET_KEY`: Flask secret key
- `DATABASE_URL`: SQLAlchemy database URL

Example values are provided in `.env.example`.

## Development Database Notes

- SQLite is used for local development.
- Database files are stored under the `instance/` folder by default.
- The `instance/` folder is ignored by Git.
- Schema changes are managed with Flask-Migrate/Alembic. Application startup does not create, delete, or alter database tables.
- Before pulling schema changes, back up the local database and run `flask --app run.py db upgrade`.

## CSV Import Expectations

The CSV importer currently requires these columns:

- `date`
- `description`
- `amount`

Optional columns:

- `household_flag`
- `notes`

Supported date formats include `YYYY-MM-DD`, `DD/MM/YYYY`, `MM/DD/YYYY`, and `DD-MM-YYYY`.

Every upload requires selecting its destination financial account. Before creating a batch, LedgerLens compares exact transaction identities across existing accounts and blocks the upload when the statement strongly overlaps a different account.

For statements imported before this guard existed, the Imports page shows verified later cross-batch duplicates. **Exclude Verified Duplicates** preserves the raw rows and import history while removing those later duplicates from balances, reviews, recurrence, and analytics. Duplicate matching requires the same account, date, amount, and cleaned description; same-batch repeated transactions are not automatically excluded.

## Running Tests

```bash
pytest -q
```

## Maintenance Commands

Enrich eligible historical bank rows from retained PayPal descriptions:

```bash
flask --app run.py backfill-paypal-descriptions
```

Apply the current categorization rules to pending transactions that are still uncategorized:

```bash
flask --app run.py backfill-categories
```

The command is idempotent and does not overwrite reviewed classifications.

Move a historical import batch to the correct financial account:

```bash
flask --app run.py reassign-import-batch --batch-id 3 --account-name "Credit Union" --account-type savings
```

The target account is created for the batch owner when it does not already exist.

PayPal imports keep two deliberate modes:

- Select a bank account to enrich/reconcile matching PayPal-funded bank transactions.
- Select an account whose type is `wallet` to retain PayPal transactions as their own ledger.

Legacy PayPal statements may contain internal funding, conversion, and authorization rows. On the Accounts page, use **Exclude Detected Internal Rows** to preserve those raw transactions while removing them from balances, reviews, recurrence detection, completeness counts, and analytics. Each exclusion records a reason and timestamp. **Restore Excluded Rows** reverses the operation.

Confirmed Credit Union internal movements remain balance-affecting ledger entries but are excluded from household income, spending, cash-flow analytics, recurrence detection, and review queues. `MNGTFEE` is treated as personal earmarked Savings; `EFT DISBUR` is treated as a personal shares-account Transfer. The Accounts page provides an explicit maintenance action, and future imports apply the same rules automatically.

## Category Automation

- Tax includes TV Licence, Local Property Tax (LPT), property tax, car tax, and motor tax.
- Insurance includes home, car/motor, health, and pet insurance outflows.
- Insurance Claims includes positive VHI and health-insurance claim/refund transactions.
- Insurance Claims default to the `household` flag. An existing category/flag link is preserved if it has already been configured.
- The dashboard reports monthly insurance spend, claims received, and net insurance cost.

## Reviewable Financial Intelligence

- Intelligence GET pages are read-only; detection and previews do not persist records.
- Recurring suggestions show observation dates, typical amount, variation, frequency, next-date estimate, and confidence before confirmation.
- Confirmed recurring rules remain editable and can be deactivated directly from Missing reports using **Not recurring**; source transactions are never deleted or reclassified by that action.
- Supported recurrence frequencies are weekly, fortnightly, monthly, quarterly, annual, and irregular.
- Merchant mappings expose origin and status, with a read-only impact preview before optional application.
- Savings recovery uses withdrawal, repayment, and adjustment events to calculate the current position.
- Dashboard reporting periods include current month, previous month, last three months, and custom ranges.
- Data-completeness warnings identify stale accounts, pending reviews, uncategorized rows, and partially represented periods.

Financial intelligence is descriptive and may be incomplete; it is not definitive financial advice.

## Changing Payment References

LedgerLens normalizes long numeric reference sequences when comparing reviewed descriptions. When at least two reviewed transactions in the same account share a normalized pattern and unanimously agree on category and household flag, a future reference variant reuses that reviewed classification automatically. The review form also supports **Apply to matching payee pattern**.

Known stable payees can use a canonical merchant identity. For example, changing `AN POST TV LIC` references map to `An Post TV Licence` while retaining the original transaction description for audit purposes.

## Payday Forecasting

The Forecast page keeps estimated planning data separate from actual transactions. It supports:

- Next payday, next 30 days, next 90 days, and custom periods.
- Weekly, fortnightly, monthly, quarterly, annual, irregular, and one-off schedules where appropriate.
- Editable income schedules and household commitments.
- One-off planned income or expenses.
- Chronological running balances, projected closing balance, and the minimum projected balance.
- Commitments due before the next configured income.
- Sinking-fund provisions with an estimated amount per payday.

Forecast and sinking-fund values are estimates for planning, not definitive financial advice. Creating forecast rows never creates actual transaction records.

## Request Security

CSRF protection is enabled by default for every state-changing form. Tests disable CSRF only through `TestConfig`; production and local development retain protection.

## Project Structure

```text
LedgerLens/
  app/
    __init__.py
    config.py
    extensions.py
    routes/
      main.py
    models/
      __init__.py
    services/
      csv_import.py
      merchant_mapping.py
      categorization.py
      duplicate_detection.py
    templates/
      base.html
      dashboard.html
      transactions.html
      imports.html
    static/
      css/
        styles.css
      js/
        scripts.js
  tests/
    conftest.py
    test_app.py
    test_models.py
    test_csv_import.py
    test_services.py
  instance/
  .env.example
  .gitignore
  requirements.txt
  run.py
  README.md
```

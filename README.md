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

4. Initialize the development database:

```bash
flask --app run.py init-db
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

## CSV Import Expectations

The CSV importer currently requires these columns:

- `date`
- `description`
- `amount`

Optional columns:

- `household_flag`
- `notes`

Supported date formats include `YYYY-MM-DD`, `DD/MM/YYYY`, `MM/DD/YYYY`, and `DD-MM-YYYY`.

## Running Tests

```bash
pytest -q
```

## Maintenance Commands

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

## Category Automation

- Tax includes TV Licence, Local Property Tax (LPT), property tax, car tax, and motor tax.
- Insurance includes home, car/motor, health, and pet insurance outflows.
- Insurance Claims includes positive VHI and health-insurance claim/refund transactions.
- Insurance Claims default to the `household` flag. An existing category/flag link is preserved if it has already been configured.
- The dashboard reports monthly insurance spend, claims received, and net insurance cost.

## Reviewable Financial Intelligence

- Intelligence GET pages are read-only; detection and previews do not persist records.
- Recurring suggestions show observation dates, typical amount, variation, frequency, next-date estimate, and confidence before confirmation.
- Supported recurrence frequencies are weekly, fortnightly, monthly, quarterly, annual, and irregular.
- Merchant mappings expose origin and status, with a read-only impact preview before optional application.
- Savings recovery uses withdrawal, repayment, and adjustment events to calculate the current position.
- Dashboard reporting periods include current month, previous month, last three months, and custom ranges.
- Data-completeness warnings identify stale accounts, pending reviews, uncategorized rows, and partially represented periods.

Financial intelligence is descriptive and may be incomplete; it is not definitive financial advice.

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

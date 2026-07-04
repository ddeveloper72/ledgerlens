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

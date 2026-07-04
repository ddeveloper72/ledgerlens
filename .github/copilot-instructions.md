# Copilot Instructions

## Project Goal

Build a secure Flask-based household finance dashboard for private local development. The application should help import, categorise, and review household financial transactions using a temporary local SQLite database during development.

Do not include any real personal, financial, banking, family, account, email, or identifying information anywhere in the source code, seed data, comments, documentation, commits, tests, or examples. Use generic sample data only.

## Core Technology Stack

* Python
* Flask
* Jinja2 templates
* Tailwind CSS
* JavaScript
* SQLite for local development
* `.venv` for Python dependencies
* Git for version control

## Development Environment

Use a local Python virtual environment named `.venv`.

Expected setup:

```bash
python -m venv .venv
```

Activate the environment before installing dependencies or running the app.

Store Python dependencies in:

```text
requirements.txt
```

Use a `.env` file for local configuration and secrets. Never commit `.env`.

Provide a `.env.example` file with placeholder values only.

## Project Structure

Use a clean Flask project structure similar to:

```text
project-root/
  app/
    __init__.py
    routes/
    models/
    services/
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
  instance/
  .env.example
  .gitignore
  requirements.txt
  run.py
  README.md
  copilot-instructions.md
```

## Styling Rules

Use Tailwind CSS for styling.

All custom CSS must be saved in:

```text
app/static/css/styles.css
```

Do not use inline styles.

Do not place CSS inside Jinja templates.

## JavaScript Rules

All JavaScript must be saved in:

```text
app/static/js/scripts.js
```

Do not use inline scripts inside templates unless absolutely necessary.

Keep JavaScript simple, readable, and progressively enhanced.

## Template Rules

Use Jinja template inheritance.

Create a shared base template:

```text
app/templates/base.html
```

All page templates should extend `base.html`.

Avoid duplicating layout, navigation, script imports, or stylesheet imports across templates.

## Database Rules

Use SQLite for local development.

Store development database files in a location excluded by `.gitignore`.

Do not commit database files.

Use generic models such as:

* User
* Account
* Transaction
* Merchant
* MerchantAlias
* Category
* ImportBatch
* RecurringBill
* SavingsGoal

Use generic seed data only.

## Security Rules

Never hardcode secrets, passwords, API keys, tokens, connection strings, account numbers, emails, or real names.

Use environment variables loaded from `.env`.

Exclude sensitive and temporary files using `.gitignore`.

Include at minimum:

```text
.env
.venv/
__pycache__/
*.pyc
instance/
*.sqlite
*.sqlite3
*.db
.DS_Store
.pytest_cache/
.coverage
htmlcov/
```

## Data Privacy Rules

Use placeholder demo data only.

Do not include real merchant names from personal accounts unless they are generic public examples such as “Sample Utility Provider” or “Example Grocery Store”.

Do not include personal names, children’s names, pet names, bank names, account numbers, real addresses, or real household details.

## Transaction Import Rules

Build CSV import support before API integrations.

The import layer should:

* accept CSV files
* validate required columns
* normalise transaction dates
* normalise amounts
* identify duplicate transactions where possible
* store imports as batches
* allow transactions to be reviewed before final categorisation

## Categorisation Rules

Create a merchant mapping layer.

A transaction should support:

* original description
* cleaned description
* mapped merchant
* category
* household flag
* notes
* import batch reference

Suggested household flags:

```text
household
personal
shared
reimbursable
unknown
```

## Comments and Code Quality

Use appropriate code comments to explain the purpose of functions, services, and non-obvious logic.

Avoid excessive comments that simply repeat the code.

Prefer small, focused functions.

Separate business logic into service modules rather than placing everything in route handlers.

## Testing

Create test scripts using `pytest`.

Include tests for:

* app creation
* database models
* CSV import validation
* transaction normalisation
* merchant mapping
* category assignment
* duplicate detection

Tests must use temporary test data only.

Do not use real financial data in tests.

## Git Workflow

Use Git from the beginning.

Make small incremental commits after meaningful changes.

Suggested commit pattern:

```text
git add .
git commit -m "Set up Flask application structure"
git commit -m "Add transaction model and SQLite configuration"
git commit -m "Add CSV import service"
git commit -m "Add merchant categorisation rules"
git commit -m "Add dashboard template"
```

Once a GitHub repository has been created and the remote has been added, push changes regularly:

```bash
git push origin main
```

Do not commit secrets, local databases, virtual environments, personal data, exported bank files, temporary scripts, or generated test artefacts.

## README Requirements

Create a `README.md` that explains:

* project purpose
* local setup
* virtual environment setup
* dependency installation
* `.env` configuration
* running the Flask app
* running tests
* development database notes
* privacy warning not to commit real financial data

## Development Priorities

Build in this order:

1. Flask app factory and project structure
2. Environment configuration
3. SQLite database setup
4. Base Jinja template
5. Dashboard page
6. Transaction model
7. CSV import service
8. Merchant mapping service
9. Category model
10. Basic reporting views
11. Tests
12. README documentation
13. Git commits and GitHub push

## Important Constraint

This project is for private household finance planning. Treat privacy, security, and clean data handling as first-class requirements from the start.


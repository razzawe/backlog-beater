# Backlog Beater backend rewrite

This folder is a small modular-monolith skeleton. The existing `services/`
implementation remains unchanged and can be used as a reference while features
are moved here one at a time.

## Structure

```text
app/
  main.py          application construction and router registration
  auth/            registration, login, and JWT code
    router.py       authentication HTTP routes
  backlog/         backlog feature
    router.py       backlog HTTP routes
  integrations/    Steam and metadata provider feature
    router.py       integration HTTP routes
```

Run locally from this directory:

```bash
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open `http://localhost:8000/health` or `http://localhost:8000/docs`.

Move one feature at a time. Keep each feature folder minimal and add files only
when a real responsibility needs somewhere to live.

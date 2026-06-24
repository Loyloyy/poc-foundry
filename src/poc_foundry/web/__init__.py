"""The M3 web UI — a SECOND thin presentation over the headless contract (design §5.12, rule #5).

Holds NO pipeline logic: it calls ``poc_foundry.core`` exactly like the CLI does. The orchestrator
process holds the secrets, so the service binds localhost ONLY and is reached over an SSH tunnel —
the tunnel is the security boundary; there is no in-app auth layer (and we don't claim one).

Layout:
  - ``runmanager.py``  pure-stdlib single-slot run controller + event fan-out (fakes-testable on 3.10)
  - ``server.py``      the FastAPI app (image-only; imports the ``ui`` extra) serving the API + SSE +
                       the committed React ``dist/``
"""

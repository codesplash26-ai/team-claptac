"""Developer view: watch the agents talk to each other.  (Owner: Dinuka)

    trace.py    the message log as data: events with who sent them, who received them, what caused them
    catalog.py  what each agent is, does, reads and writes, in plain language, plus live activity

Served under ``/dev`` by ``atlas/api/dev_routes.py`` (to be added). Read-only: nothing here can change
system state. Cause-and-effect between events needs the bus to record it (a small ``event_causes``
table beside the ledger), which is the first task here and touches ``atlas/core/event_bus.py``: tell
Aeshan before changing it.
"""

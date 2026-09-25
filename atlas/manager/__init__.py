"""The ATLAS agent manager: a control plane for the agent system.  (Owner: Dinuka)

Aspects, each a module here, that turn the agents from something that runs into something that can be
operated, observed, judged and governed:

    registry.py     the inventory: every agent, its version, status and environments, with real
                    lifecycle controls (suspend and resume: the bus stops delivering, nothing is lost)
    observer.py     traces in the OpenTelemetry model (``gen_ai.*`` attributes), and OTLP export so any
                    tracing backend can ingest them
    evaluation/     evaluators (span, trace and agent level) that score how well the agents did; monitors
                    run them on live traces on a schedule; experiments run them on scenario replays
    rbac.py         ``resource:verb`` permissions, declared on every route, and predefined roles
    audit.py        an automatic, redacted trail of everything that changes the system
    api.py          the ``/am/v1`` control-plane API (controllers over these services)
    cli.py          the same API from a terminal: ``python -m atlas.manager.cli``

The bus and the Gateway already time every handler and executor call, so the agents are instrumented by
construction; the observer only presents what they recorded.
"""

ORG = "atlas"
VERSION = "0.1.0"

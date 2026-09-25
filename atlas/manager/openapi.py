"""The committed API contract.  (Owner: Dinuka)

``docs/manager_openapi.json`` is the reviewed description of ``/am/v1``. Changing an endpoint means
regenerating it (``python -m atlas.manager.openapi``) and committing the result, so an API change is
always visible in a diff, and a test fails if the two disagree.
"""

from __future__ import annotations

import json
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "docs" / "manager_openapi.json"


def spec(app) -> dict:
    full = app.openapi()
    paths = {p: v for p, v in full["paths"].items() if p.startswith("/am/v1")}
    used = json.dumps(paths)
    schemas = {
        name: body
        for name, body in full.get("components", {}).get("schemas", {}).items()
        if f'"#/components/schemas/{name}"' in used
    }
    # schemas can reference each other: keep everything reachable
    changed = True
    while changed:
        changed = False
        blob = json.dumps(schemas)
        for name, body in full.get("components", {}).get("schemas", {}).items():
            if name not in schemas and f'"#/components/schemas/{name}"' in blob:
                schemas[name] = body
                changed = True
    return {
        "openapi": full["openapi"],
        "info": {"title": "ATLAS agent manager", "version": full["info"]["version"]},
        "paths": dict(sorted(paths.items())),
        "components": {"schemas": dict(sorted(schemas.items()))},
    }


def render(app) -> str:
    return json.dumps(spec(app), indent=1, sort_keys=True) + "\n"


def main() -> None:
    from atlas.api.main import create_app

    PATH.write_text(render(create_app()), encoding="utf-8")
    print("wrote", PATH)


if __name__ == "__main__":
    main()

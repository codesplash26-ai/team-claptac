"""Rebuildable temporal audit graph, including explicit depends_on links."""

import networkx as nx


class KnowledgeGraph:
    def __init__(self):
        self.graph = nx.MultiDiGraph()

    def rebuild(self, ledger):
        if not ledger.verify():
            raise ValueError("Ledger integrity check failed")
        self.graph.clear()
        for record in ledger.records("event"):
            event = record["payload"]
            key = "event:" + event["id"]
            self.graph.add_node(
                key, type=event["type"], timestamp=event["created_at"], sequence=record["seq"]
            )
            links = [("source:" + event["source"], "reported_by")]
            if event.get("incident_id"):
                incident = event["incident_id"]
                links.append(
                    (
                        incident if incident.startswith("incident:") else "incident:" + incident,
                        "part_of",
                    )
                )
            payload = event["payload"]
            links += [
                ("evidence:" + ref, "supported_by") for ref in payload.get("evidence_ids", [])
            ]
            links += [(ref, "depends_on") for ref in payload.get("depends_on", [])]
            for target, relation in links:
                self.graph.add_edge(key, target, key=relation, relation=relation)
        return self.graph

    def affected_by(self, subject):
        if subject not in self.graph:
            return []
        return sorted(nx.ancestors(self.graph, subject))

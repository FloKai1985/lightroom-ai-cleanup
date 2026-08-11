"""Generic graph clustering used to turn pairwise "similar" edges into groups.

Kept separate from `candidate_groups.py` so the graph algorithm (connected
components over an edge list) can be tested independently of the
photo-domain similarity rules that produce the edges.
"""

from __future__ import annotations


def connected_components(node_ids: list[int], edges: list[tuple[int, int]]) -> list[list[int]]:
    """Return connected components of the undirected graph `(node_ids, edges)`.

    Nodes with no edges are returned as singleton components. Each returned
    component is sorted ascending; components are returned in order of their
    smallest member.
    """
    parent: dict[int, int] = {n: n for n in node_ids}

    def find(n: int) -> int:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for a, b in edges:
        union(a, b)

    groups: dict[int, list[int]] = {}
    for n in node_ids:
        groups.setdefault(find(n), []).append(n)

    components = [sorted(members) for members in groups.values()]
    components.sort(key=lambda members: members[0])
    return components

"""输入校验：节点、管段、检修口的全部规则。

任何失败都抛出 AuditError，携带可定位信息 {"row", "field"}，
页面据此把错误标到对应输入处；全局错误 loc 为 None。
失败时由上层保证保留输入、清除旧结论。
"""

from __future__ import annotations

from collections import defaultdict

from .cpp import AuditError

MAX_NODES = 18
MIN_NODES = 2
MAX_EDGES = 32
MIN_EDGES = 1


def _ascii_ok(s: str) -> bool:
    try:
        s.encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def validate(raw):
    """raw: {"nodes": [str...], "edges": [{id,u,v,length}...], "depot": str}

    返回规整化后的 (nodes, edges, depot)；nodes 保持页面顺序去重，
    edges 保持页面顺序。校验通过即保证：
      2..18 个唯一 ASCII 非空节点；
      1..32 条边，标识唯一且为非空 ASCII，长度为正整数，
      端点均存在，无自环（允许平行边）；
      检修口存在；基础图连通（每条管段均可达）。
    """
    if not isinstance(raw, dict):
        raise AuditError("请求体格式错误", "bad_request")
    node_rows = raw.get("nodes")
    edge_rows = raw.get("edges")
    depot = raw.get("depot")

    if not isinstance(node_rows, list) or not isinstance(edge_rows, list):
        raise AuditError("节点表或管段表格式错误", "bad_request")

    # ---- 节点 ----
    nodes = []
    seen = set()
    for i, name in enumerate(node_rows):
        if not isinstance(name, str):
            raise AuditError("节点名必须是文本", "node_not_ascii", {"row": i, "field": "node"})
        n = name.strip()
        if not n:
            raise AuditError("节点名不能为空", "node_empty", {"row": i, "field": "node"})
        if not _ascii_ok(n):
            raise AuditError("节点名只能含 ASCII 字符", "node_not_ascii", {"row": i, "field": "node"})
        if n in seen:
            raise AuditError(f"节点 {n} 重复", "node_duplicate", {"row": i, "field": "node"})
        seen.add(n)
        nodes.append(n)

    if not (MIN_NODES <= len(nodes) <= MAX_NODES):
        raise AuditError(
            f"节点数必须在 {MIN_NODES} 到 {MAX_NODES} 之间（当前 {len(nodes)}）",
            "node_count",
        )

    # ---- 管段 ----
    edges = []
    edge_ids = set()
    for i, row in enumerate(edge_rows):
        if not isinstance(row, dict):
            raise AuditError("管段行格式错误", "bad_edge", {"row": i})
        eid = row.get("id", "")
        if not isinstance(eid, str):
            raise AuditError("管段标识必须是文本", "edge_id_invalid", {"row": i, "field": "id"})
        eid = eid.strip()
        if not eid:
            raise AuditError("管段标识不能为空", "edge_id_empty", {"row": i, "field": "id"})
        if not _ascii_ok(eid):
            raise AuditError("管段标识只能含 ASCII 字符", "edge_id_not_ascii", {"row": i, "field": "id"})
        if eid in edge_ids:
            raise AuditError(f"管段标识 {eid} 重复", "edge_id_duplicate", {"row": i, "field": "id"})

        u = row.get("u", "")
        v = row.get("v", "")
        for field, endpoint in (("u", u), ("v", v)):
            if not isinstance(endpoint, str) or not endpoint.strip():
                raise AuditError("端点不能为空", "endpoint_empty", {"row": i, "field": field})
        u, v = u.strip(), v.strip()
        if not _ascii_ok(u) or not _ascii_ok(v):
            raise AuditError("端点只能含 ASCII 字符", "endpoint_not_ascii", {"row": i})
        if u not in seen:
            raise AuditError(f"未知端点 {u}", "unknown_endpoint", {"row": i, "field": "u"})
        if v not in seen:
            raise AuditError(f"未知端点 {v}", "unknown_endpoint", {"row": i, "field": "v"})
        if u == v:
            raise AuditError("禁止自环管段", "self_loop", {"row": i, "field": "v"})

        length = row.get("length")
        # 必须是正整数（拒绝小数、非数字、0 与负数）
        if isinstance(length, bool) or not isinstance(length, int) or length <= 0:
            raise AuditError("管段长度必须为正整数", "bad_length", {"row": i, "field": "length"})

        edge_ids.add(eid)
        edges.append({"id": eid, "u": u, "v": v, "length": length})

    if not (MIN_EDGES <= len(edges) <= MAX_EDGES):
        raise AuditError(
            f"管段数必须在 {MIN_EDGES} 到 {MAX_EDGES} 之间（当前 {len(edges)}）",
            "edge_count",
        )

    # ---- 检修口 ----
    if not isinstance(depot, str) or not depot.strip():
        raise AuditError("请选择检修口", "depot_missing")
    depot = depot.strip()
    if depot not in seen:
        raise AuditError(f"检修口 {depot} 不在节点表中", "depot_unknown", {"field": "depot"})

    # ---- 连通性（无向，并查集）----
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, e in enumerate(edges):
        ru, rv = find(e["u"]), find(e["v"])
        if ru != rv:
            parent[ru] = rv

    root = find(depot)
    for n in nodes:
        if find(n) != root:
            # 定位到该孤立节点在页面中的行（node_rows 可能带空白，逐行比对）
            idx = next((i for i, raw in enumerate(node_rows)
                        if isinstance(raw, str) and raw.strip() == n), 0)
            raise AuditError(
                f"管网断连：节点 {n} 无法从检修口 {depot} 到达",
                "disconnected",
                {"row": idx, "field": "node"},
            )

    return nodes, edges, depot

"""中国邮递员问题（无向图最小增程）精确求解。

在原有每条管段均走一次的基础上，选择额外重复的管段集合 D，使各节点
度数均为偶数（每个奇度点被 D 中奇数条边覆盖）且增加长度最小。

精确枚举 2^m 个 0/1 重复向量（m <= 32）：把边按标识 ASCII 顺序分为
4 组（每组 <= 8 条），组内枚举并按“奇度点奇偶综合症”聚合，再做两组
XOR 卷积后只在目标综合症上合并。聚合携带：
  * 最小费用与达到它的精确集合数（Python 大整数计数）；
  * 各位置在全部同优集合中的与/或掩码 -> 必重复 / 可重复 / 从不重复；
  * 组内 0 优先字典序最优代表（用位权 2^(m-1-pos) 的大整数比较）。

规范集合即所有同优集合中按边标识顺序 0 优先位向量最小者。
最后在“原管段 + 重复副本”的多重图上跑确定性 Hierholzer，得到一条
从检修口起止、逐步可核对且恰好使用相应副本数的闭合路线。
"""

from __future__ import annotations

from collections import defaultdict


class AuditError(Exception):
    """可定位的审计失败。loc 为 None 表示全局错误。"""

    def __init__(self, message, code, loc=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.loc = loc

    def to_payload(self):
        out = {"code": self.code, "message": self.message}
        if self.loc is not None:
            out["loc"] = self.loc
        return out


def _balanced_chunks(seq, k):
    """尽量均衡地保持顺序分块（块数可能少于 k）。"""
    n = len(seq)
    sizes = [n // k + (1 if i < n % k else 0) for i in range(k)]
    out, i = [], 0
    for s in sizes:
        if s:
            out.append(seq[i : i + s])
            i += s
    return out


def _agg_take(table, syn, cost, bits, and_neutral=0):
    """把一个可行选择并入按综合症聚合的字典。

    bits 为全局高位编码：位置 p 的边入选当且仅当第 (m-1-p) 位为 1；
    数值比较即边标识顺序的二进制字典序（0 优先）。
    and_neutral：与本选择无关的位置（高位掩码），在与掩码中填 1，
    即 AND 的中性元，避免跨组合并时把“不涉及”误判为“必不选”。
    """
    andbits = bits | and_neutral
    old = table.get(syn)
    if old is None or cost < old["cost"]:
        table[syn] = {
            "cost": cost,
            "count": 1,
            "andmask": andbits,
            "ormask": bits,
            "lexbits": bits,
        }
    elif cost == old["cost"]:
        old["count"] += 1
        old["andmask"] &= andbits
        old["ormask"] |= bits
        if bits < old["lexbits"]:
            old["lexbits"] = bits


def _merge(a, b):
    """两张综合症表的 XOR 卷积。"""
    out = {}
    for sa, pa in a.items():
        for sb, pb in b.items():
            syn = sa ^ sb
            cost = pa["cost"] + pb["cost"]
            cnt = pa["count"] * pb["count"]
            andm = pa["andmask"] & pb["andmask"]
            orm = pa["ormask"] | pb["ormask"]
            lex = pa["lexbits"] | pb["lexbits"]
            old = out.get(syn)
            if old is None or cost < old["cost"]:
                out[syn] = {"cost": cost, "count": cnt, "andmask": andm,
                            "ormask": orm, "lexbits": lex}
            elif cost == old["cost"]:
                old["count"] += cnt
                old["andmask"] &= andm
                old["ormask"] |= orm
                if lex < old["lexbits"]:
                    old["lexbits"] = lex
    return out


def _enumerate_group(ordered, positions, m, edge_syn):
    """枚举一组边的所有选择，按综合症聚合（每组 <= 8 条，至多 256 次）。"""
    table = {}
    k = len(positions)
    # 本组不涉及的位置：与掩码填 1（中性），或掩码天然为 0
    group_bits = 0
    for p in positions:
        group_bits |= 1 << (m - 1 - p)
    neutral = ((1 << m) - 1) ^ group_bits
    for z in range(1 << k):
        syn, cost, bits = 0, 0, 0
        for j in range(k):
            if (z >> j) & 1:
                p = positions[j]
                syn ^= edge_syn[p]
                cost += ordered[p]["length"]
                bits |= 1 << (m - 1 - p)
        _agg_take(table, syn, cost, bits, and_neutral=neutral)
    return table


def _euler_tour(sorted_edges, copies, length_of, depot):
    """确定性 Hierholzer。邻接副本按 (edge_id, copy_no) 排序。

    返回 (tour_nodes, steps, passes)。
    """
    incident = defaultdict(list)
    unused = {}
    for u, v, eid in sorted_edges:
        for c in range(copies[eid]):
            key = (eid, c)
            unused[key] = (u, v)
            incident[u].append(key)
            incident[v].append(key)
    for lst in incident.values():
        lst.sort()

    stack = [(depot, None)]
    pops = []
    while stack:
        v = stack[-1][0]
        chosen = None
        while incident[v]:
            key = incident[v][0]
            if key in unused:
                chosen = key
                break
            incident[v].pop(0)
        if chosen is None:
            pops.append(stack.pop())
            continue
        incident[v].remove(chosen)
        u, w = unused.pop(chosen)
        nxt = w if v == u else u
        stack.append((nxt, chosen))

    pops.reverse()
    tour_nodes = [p[0] for p in pops]
    tour_keys = [p[1] for p in pops[1:]]

    expected = sum(copies.values())
    if len(tour_keys) != expected:
        raise AuditError("路线生成失败：管网可能不连通", "disconnected")
    if tour_nodes[0] != depot or tour_nodes[-1] != depot:
        raise AuditError("路线生成失败：路线未在检修口闭合", "route_not_closed")

    steps = []
    passes = defaultdict(list)
    for i, key in enumerate(tour_keys):
        a, b = tour_nodes[i], tour_nodes[i + 1]
        eid, cno = key
        steps.append({
            "seq": i + 1,
            "edge_id": eid,
            "copy": cno,  # 0 = 原管段，1 = 重复副本
            "from": a,
            "to": b,
            "length": length_of[eid],
        })
        passes[eid].append(i + 1)
    return tour_nodes, steps, dict(passes)


def solve(edges, nodes, depot):
    """edges/nodes 已通过 validate。edges 顺序为页面顺序，内部重排。"""
    ordered = sorted(edges, key=lambda e: e["id"].encode("ascii"))
    m = len(ordered)
    length_of = {e["id"]: e["length"] for e in ordered}

    degree = {n: 0 for n in nodes}
    for e in ordered:
        degree[e["u"]] += 1
        degree[e["v"]] += 1
    odd = [n for n in nodes if degree[n] % 2 == 1]
    node_index = {n: i for i, n in enumerate(nodes)}
    # 综合症在【全部节点】空间：非奇度点也必须被偶数条重复边覆盖，
    # 否则其偶度性会被破坏。目标综合症 = 奇度点掩码。
    target = 0
    for n in odd:
        target |= 1 << node_index[n]

    edge_syn = [0] * m
    for i, e in enumerate(ordered):
        edge_syn[i] = (1 << node_index[e["u"]]) | (1 << node_index[e["v"]])

    fullmask = (1 << m) - 1
    if not odd:
        # 欧拉管网：零增程是唯一最优（任何非空可行集费用均为正）。
        best_cost, total_count = 0, 1
        andmask, ormask, canon = 0, 0, 0
    else:
        groups = _balanced_chunks(list(range(m)), 4)
        while len(groups) < 4:
            groups.append([])  # 空组 -> 仅含综合症 0 的恒等表
        tables = [_enumerate_group(ordered, g, m, edge_syn) for g in groups]
        d12 = _merge(tables[0], tables[1])
        d34 = _merge(tables[2], tables[3])

        best_cost = None
        total_count = 0
        andmask, ormask, canon = fullmask, 0, 0
        for sa, pa in d12.items():
            pb = d34.get(target ^ sa)
            if pb is None:
                continue
            cost = pa["cost"] + pb["cost"]
            cnt = pa["count"] * pb["count"]
            andm = pa["andmask"] & pb["andmask"]
            orm = pa["ormask"] | pb["ormask"]
            lex = pa["lexbits"] | pb["lexbits"]
            if best_cost is None or cost < best_cost:
                best_cost, total_count = cost, cnt
                andmask, ormask, canon = andm, orm, lex
            elif cost == best_cost:
                total_count += cnt
                andmask &= andm
                ormask |= orm
                if lex < canon:
                    canon = lex

    canonical_ids = []
    classification = {}
    for i, e in enumerate(ordered):
        hi = 1 << (m - 1 - i)
        if canon & hi:
            canonical_ids.append(e["id"])
        if andmask & hi:
            cls = "required"
        elif ormask & hi:
            cls = "optional"
        else:
            cls = "never"
        classification[e["id"]] = cls

    canon_set = set(canonical_ids)
    copies = {e["id"]: (2 if e["id"] in canon_set else 1) for e in ordered}

    sorted_edges = [(e["u"], e["v"], e["id"]) for e in ordered]
    tour_nodes, steps, passes = _euler_tour(sorted_edges, copies, length_of, depot)

    base_length = sum(length_of.values())
    vector = "".join("1" if e["id"] in canon_set else "0" for e in ordered)

    return {
        "base_length": base_length,
        "added_length": best_cost,
        "total_length": base_length + best_cost,
        "optimal_count": total_count,
        "edge_order": [e["id"] for e in ordered],
        "canonical_vector": vector,
        "canonical_duplicates": canonical_ids,
        "classification": classification,
        "copies": copies,
        "steps": steps,
        "edge_passes": passes,
        "odd_nodes": odd,
        "depot": depot,
        "tour_nodes": tour_nodes,
        "nodes": nodes,
        "edges": [
            {"id": e["id"], "u": e["u"], "v": e["v"], "length": e["length"]}
            for e in ordered
        ],
    }

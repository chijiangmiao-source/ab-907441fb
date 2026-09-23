"""核心算法测试：典型算例 + 随机图暴力枚举对照 + 路线逐步合法性。"""

import itertools
import random
import time
import unittest

from app.cpp import AuditError, solve


def brute_force(edges, nodes, depot):
    """2^m 枚举全部重复向量，返回 (最优费用, 解集合列表[位串])。"""
    ordered = sorted(edges, key=lambda e: e["id"])
    m = len(ordered)
    degree = {n: 0 for n in nodes}
    for e in ordered:
        degree[e["u"]] += 1
        degree[e["v"]] += 1
    odd = {n for n in nodes if degree[n] % 2}
    best, sols = None, []
    for z in range(1 << m):
        cover = set()
        cost = 0
        for i in range(m):
            if (z >> i) & 1:
                e = ordered[i]
                cover ^= {e["u"]}
                cover ^= {e["v"]}
                cost += e["length"]
        if cover != odd:
            continue
        if best is None or cost < best:
            best, sols = cost, [z]
        elif cost == best:
            sols.append(z)
    return best, sols, ordered


def expect_classification(sols, m):
    req, never = (1 << m) - 1, 0
    for z in sols:
        req &= z
        never |= z
    cls = []
    for i in range(m):
        if req & (1 << i):  # z 的第 i 位（数值低位）即 ordered[i]
            cls.append("required")
        elif not (never & (1 << i)):
            cls.append("never")
        else:
            cls.append("optional")
    return cls


def lex_key(z, m):
    """把重复向量 z（位 i = ordered[i]）转成首位为高位的字典序数值。"""
    k = 0
    for i in range(m):
        if (z >> i) & 1:
            k |= 1 << (m - 1 - i)
    return k


def assert_route_valid(test, r, depot):
    steps = r["steps"]
    # 起止检修口
    test.assertEqual(steps[0]["from"], depot)
    test.assertEqual(steps[-1]["to"], depot)
    # 逐步衔接
    for a, b in zip(steps, steps[1:]):
        test.assertEqual(a["to"], b["from"])
    # 每步真实连接端点
    ends = {e["id"]: (e["u"], e["v"]) for e in r["edges"]}
    for s in steps:
        u, v = ends[s["edge_id"]]
        test.assertTrue({s["from"], s["to"]} == {u, v})
        test.assertEqual(s["length"], next(e["length"] for e in r["edges"] if e["id"] == s["edge_id"]))
    # 副本数恰好：原管段一次 + 规范重复边一次
    use = {}
    for s in steps:
        use[(s["edge_id"], s["copy"])] = use.get((s["edge_id"], s["copy"]), 0) + 1
    for e in r["edges"]:
        test.assertEqual(use.get((e["id"], 0), 0), 1)
        if e["id"] in r["canonical_duplicates"]:
            test.assertEqual(use.get((e["id"], 1), 0), 1)
        else:
            test.assertNotIn((e["id"], 1), use)
    # 长度合计
    test.assertEqual(sum(s["length"] for s in steps), r["total_length"])
    # 经过位置索引
    for eid, pass_list in r["edge_passes"].items():
        self_pos = [s["seq"] for s in steps if s["edge_id"] == eid]
        test.assertEqual(pass_list, self_pos)


def assert_matches_brute(test, edges, nodes, depot):
    r = solve([dict(e) for e in edges], list(nodes), depot)
    best, sols, ordered = brute_force(edges, nodes, depot)
    test.assertTrue(sols, "至少应存在一个可行增程集（图连通）")
    test.assertEqual(r["added_length"], best)
    test.assertEqual(r["optimal_count"], len(sols))
    m = len(ordered)
    # 规范集合：所有同优位串中按标识顺序 0 优先（首位权重最高）
    canon_num = min(sols, key=lambda z: lex_key(z, m))
    canon_vec = "".join("1" if (canon_num >> i) & 1 else "0" for i in range(m))
    test.assertEqual(r["canonical_vector"], canon_vec)
    # 分类
    exp_cls = expect_classification(sols, m)
    for i, e in enumerate(ordered):
        test.assertEqual(r["classification"][e["id"]], exp_cls[i])
    # 基础长度/总长度
    base = sum(e["length"] for e in edges)
    test.assertEqual(r["base_length"], base)
    test.assertEqual(r["total_length"], base + best)
    assert_route_valid(test, r, depot)
    return r


class KnownGraphs(unittest.TestCase):
    def test_path_unique(self):
        # 路径 A-B-C：唯一可行集 {e1,e2}
        edges = [
            {"id": "e1", "u": "A", "v": "B", "length": 3},
            {"id": "e2", "u": "B", "v": "C", "length": 4},
        ]
        r = assert_matches_brute(self, edges, ["A", "B", "C"], "B")
        self.assertEqual(r["canonical_duplicates"], ["e1", "e2"])
        self.assertEqual(r["classification"]["e1"], "required")
        self.assertEqual(r["added_length"], 7)
        self.assertEqual(r["optimal_count"], 1)

    def test_eulerian_triangle_zero(self):
        # 三角形：所有点度数为 2，零增程边界
        edges = [
            {"id": "e1", "u": "A", "v": "B", "length": 3},
            {"id": "e2", "u": "B", "v": "C", "length": 4},
            {"id": "e3", "u": "C", "v": "A", "length": 5},
        ]
        r = assert_matches_brute(self, edges, ["A", "B", "C"], "A")
        self.assertEqual(r["added_length"], 0)
        self.assertEqual(r["optimal_count"], 1)
        self.assertEqual(r["canonical_duplicates"], [])
        self.assertEqual(r["canonical_vector"], "000")
        self.assertTrue(all(c == "never" for c in r["classification"].values()))
        self.assertEqual(len(r["steps"]), 3)

    def test_star_three_matching_vs_joinset(self):
        # K1,3：4 个奇度点 {O,a,b,c}。完美匹配有 3 种，但三种匹配的
        # 路径边集对称差都是同一个集合 {x,y,z}（中心必须净增加奇数次）。
        # 这正是“匹配数 != 同优边集数”的关键反例：同优集合数应为 1。
        edges = [
            {"id": "x", "u": "O", "v": "a", "length": 1},
            {"id": "y", "u": "O", "v": "b", "length": 1},
            {"id": "z", "u": "O", "v": "c", "length": 1},
        ]
        r = assert_matches_brute(self, edges, ["O", "a", "b", "c"], "O")
        self.assertEqual(r["optimal_count"], 1)
        self.assertEqual(r["added_length"], 3)
        self.assertTrue(all(c == "required" for c in r["classification"].values()))
        self.assertEqual(r["canonical_vector"], "111")

    def test_matchings_collide_partial(self):
        # 更一般的反例：构造匹配数与边集数不同的图，由暴力对照兜底。
        # 三角形 abc 各带一条悬边（中心图 O 不需要）：
        # 节点 a,b,c 度数 3(奇)，x,y,z 度数 1(奇)。
        edges = [
            {"id": "e1", "u": "a", "v": "x", "length": 1},
            {"id": "e2", "u": "b", "v": "y", "length": 1},
            {"id": "e3", "u": "c", "v": "z", "length": 1},
            {"id": "e4", "u": "a", "v": "b", "length": 2},
            {"id": "e5", "u": "b", "v": "c", "length": 2},
            {"id": "e6", "u": "c", "v": "a", "length": 2},
        ]
        r = assert_matches_brute(self, edges, ["a", "b", "c", "x", "y", "z"], "a")
        # 结果以暴力为准，仅断言三条悬边必被重复（奇度叶子只有一条边）
        self.assertEqual(r["classification"]["e1"], "required")
        self.assertEqual(r["classification"]["e2"], "required")
        self.assertEqual(r["classification"]["e3"], "required")

    def test_two_equal_paths(self):
        # 奇度 A,C：路径 A-B-C 与 A-D-C 等费用，直连边更贵
        edges = [
            {"id": "p1", "u": "A", "v": "B", "length": 3},
            {"id": "p2", "u": "A", "v": "D", "length": 2},
            {"id": "p3", "u": "B", "v": "C", "length": 4},
            {"id": "p4", "u": "C", "v": "D", "length": 5},
            {"id": "p5", "u": "A", "v": "C", "length": 10},
        ]
        r = assert_matches_brute(self, edges, ["A", "B", "C", "D"], "A")
        self.assertEqual(r["optimal_count"], 2)
        self.assertEqual(r["added_length"], 7)
        self.assertEqual(r["classification"]["p5"], "never")
        for pid in ("p1", "p2", "p3", "p4"):
            self.assertEqual(r["classification"][pid], "optional")
        # {p1,p3}=10100 vs {p2,p4}=01010，0 优先取后者
        self.assertEqual(r["canonical_vector"], "01010")

    def test_parallel_three(self):
        # A-B 三条平行边：奇度 A,B；选奇数条，单条费用最低，3 个同优
        edges = [
            {"id": "e1", "u": "A", "v": "B", "length": 1},
            {"id": "e2", "u": "A", "v": "B", "length": 1},
            {"id": "e3", "u": "A", "v": "B", "length": 1},
        ]
        r = assert_matches_brute(self, edges, ["A", "B"], "A")
        self.assertEqual(r["optimal_count"], 3)
        self.assertEqual(r["added_length"], 1)
        self.assertTrue(all(c == "optional" for c in r["classification"].values()))
        self.assertEqual(r["canonical_vector"], "001")

    def test_parallel_two_is_eulerian(self):
        edges = [
            {"id": "e1", "u": "A", "v": "B", "length": 5},
            {"id": "e2", "u": "A", "v": "B", "length": 5},
        ]
        r = assert_matches_brute(self, edges, ["A", "B"], "B")
        self.assertEqual(r["added_length"], 0)
        self.assertEqual(len(r["steps"]), 2)

    def test_square_with_diagonal_unique(self):
        edges = [
            {"id": "e1", "u": "A", "v": "B", "length": 10},
            {"id": "e2", "u": "B", "v": "C", "length": 10},
            {"id": "e3", "u": "C", "v": "D", "length": 10},
            {"id": "e4", "u": "D", "v": "A", "length": 10},
            {"id": "e5", "u": "A", "v": "C", "length": 7},
        ]
        r = assert_matches_brute(self, edges, ["A", "B", "C", "D"], "D")
        # 奇度 A,C；e5 直达费用 7 唯一最优
        self.assertEqual(r["canonical_duplicates"], ["e5"])
        self.assertEqual(r["classification"]["e5"], "required")


class RandomGraphs(unittest.TestCase):
    def test_random_brute_force(self):
        rng = random.Random(20260923)
        for trial in range(120):
            n = rng.randint(2, 7)
            nodes = [f"v{i}" for i in range(n)]
            # 先生成一棵生成树保证连通，再随机加边
            edges, used = [], set()
            order = nodes[:]
            rng.shuffle(order)
            idx = 0
            for k in range(1, n):
                u = order[rng.randint(0, k - 1)]
                v = order[k]
                eid = f"e{idx}"
                idx += 1
                edges.append({"id": eid, "u": u, "v": v, "length": rng.randint(1, 9)})
                used.add(frozenset((u, v)))
            extra = rng.randint(0, 6)
            for _ in range(extra):
                u, v = rng.sample(nodes, 2)
                # 允许平行边：不按端点去重，但限制总边数
                if idx >= 14:
                    break
                eid = f"e{idx}"
                idx += 1
                edges.append({"id": eid, "u": u, "v": v, "length": rng.randint(1, 9)})
            depot = rng.choice(nodes)
            with self.subTest(trial=trial, n=n, m=len(edges)):
                assert_matches_brute(self, edges, nodes, depot)


class FullScalePerf(unittest.TestCase):
    def test_32_edges_runtime(self):
        # 18 节点 32 边的压力规模：连通随机图
        rng = random.Random(4242)
        nodes = [f"N{i:02d}" for i in range(18)]
        edges = []
        order = nodes[:]
        rng.shuffle(order)
        for k in range(1, 18):
            u = order[rng.randint(0, k - 1)]
            edges.append({"id": f"E{k-1:02d}", "u": u, "v": order[k],
                          "length": rng.randint(1, 100)})
        while len(edges) < 32:
            u, v = rng.sample(nodes, 2)
            edges.append({"id": f"E{len(edges):02d}", "u": u, "v": v,
                          "length": rng.randint(1, 100)})
        t0 = time.time()
        r = solve(edges, nodes, "N00")
        dt = time.time() - t0
        self.assertLess(dt, 15.0, f"32 边求解耗时 {dt:.2f}s 过长")
        assert_route_valid(self, r, "N00")
        self.assertEqual(len(r["steps"]), 32 + len(r["canonical_duplicates"]))


if __name__ == "__main__":
    unittest.main()

"""verify：单次审计校验服务。

依次核对并以退出码报告：
  1. 奇度管网的同优分类（计数、必/可/从不、规范 0 优先、闭合路线）；
  2. 欧拉管网的零增程边界（唯一最优为空集）；
  3. 代码测试（unittest 全套）；
  4. 构建完整性（全部源码可编译、应用可导入）；
  5. HTTP 冒烟（健康检查 + 审计接口）。

任何一项失败：打印原因并以非零退出码结束；全部通过退出 0。
WEB_URL 环境变量指定被测服务（Compose 内 http://web:8000，
本机默认 http://127.0.0.1:${PORT:-8000}）。
"""

from __future__ import annotations

import json
import os
import py_compile
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from app.cpp import solve

ROOT = Path(__file__).resolve().parent.parent

PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, str, str]] = []


def report(section, ok, detail=""):
    results.append((section, PASS if ok else FAIL, detail))
    print(f"[{PASS if ok else FAIL}] {section}" + (f" — {detail}" if detail else ""), flush=True)


# ---------- 1. 奇度管网同优分类 ----------

def check_odd_graph():
    # 奇度点 {A,C}；两条等费用最短重复路径：
    #   {p1,p3}（A-B-C，3+4=7）与 {p2,p4}（A-D-C，2+5=7）；
    #   p5 直达费用 10 从不重复。
    edges = [
        {"id": "p1", "u": "A", "v": "B", "length": 3},
        {"id": "p2", "u": "A", "v": "D", "length": 2},
        {"id": "p3", "u": "B", "v": "C", "length": 4},
        {"id": "p4", "u": "C", "v": "D", "length": 5},
        {"id": "p5", "u": "A", "v": "C", "length": 10},
    ]
    nodes = ["A", "B", "C", "D"]
    r = solve(edges, nodes, "A")
    checks = [
        ("同优集合数量为 2", r["optimal_count"] == 2),
        ("最小增加长度为 7", r["added_length"] == 7),
        ("总长度等于原长+增程",
         r["total_length"] == r["base_length"] + 7 == 31),
        ("规范集合为 0 优先 {p2,p4}（位向量 01010）",
         r["canonical_vector"] == "01010" and
         r["canonical_duplicates"] == ["p2", "p4"]),
        ("p1/p2/p3/p4 可重复",
         all(r["classification"][x] == "optional" for x in ("p1", "p2", "p3", "p4"))),
        ("p5 从不重复", r["classification"]["p5"] == "never"),
        ("奇度点恰好为 A,C", set(r["odd_nodes"]) == {"A", "C"}),
        ("规范集边使用两个副本，其余一个",
         r["copies"] == {"p1": 1, "p2": 2, "p3": 1, "p4": 2, "p5": 1}),
    ]

    # 路线逐步可核对：起止于检修口、衔接正确、副本数精确、长度合计
    steps = r["steps"]
    checks.append(("路线自检修口 A 起止", steps[0]["from"] == "A" and steps[-1]["to"] == "A"))
    checks.append(("路线逐步衔接",
                   all(a["to"] == b["from"] for a, b in zip(steps, steps[1:]))))
    use: dict = {}
    for s in steps:
        use[(s["edge_id"], s["copy"])] = use.get((s["edge_id"], s["copy"]), 0) + 1
    checks.append(("每条原管段恰好 1 次",
                   all(use.get((e["id"], 0)) == 1 for e in edges)))
    checks.append(("规范重复边恰好多 1 个副本",
                   use.get(("p2", 1)) == 1 and use.get(("p4", 1)) == 1 and
                   ("p1", 1) not in use and ("p3", 1) not in use and ("p5", 1) not in use))
    checks.append(("路线长度合计为 31", sum(s["length"] for s in steps) == 31))
    # 经过位置索引自洽
    checks.append(("边经过位置索引自洽",
                   all([s["seq"] for s in steps if s["edge_id"] == eid] == plist
                       for eid, plist in r["edge_passes"].items())))

    bad = [name for name, ok in checks if not ok]
    report("奇度管网：同优集合计数/三分类/规范集/闭合路线",
           not bad, "全部 11 项断言通过" if not bad else "; ".join(bad))


# ---------- 2. 欧拉管网零增程边界 ----------

def check_eulerian_boundary():
    # 三角形：各点度数 2；以及双平行边（多重图欧拉开）
    tri = solve(
        [
            {"id": "e1", "u": "A", "v": "B", "length": 3},
            {"id": "e2", "u": "B", "v": "C", "length": 4},
            {"id": "e3", "u": "C", "v": "A", "length": 5},
        ],
        ["A", "B", "C"], "A",
    )
    para = solve(
        [
            {"id": "a", "u": "X", "v": "Y", "length": 7},
            {"id": "b", "u": "X", "v": "Y", "length": 9},
        ],
        ["X", "Y"], "X",
    )
    ok = (
        tri["added_length"] == 0 and tri["optimal_count"] == 1
        and tri["canonical_duplicates"] == [] and tri["canonical_vector"] == "000"
        and all(c == "never" for c in tri["classification"].values())
        and tri["total_length"] == 12 and len(tri["steps"]) == 3
        and para["added_length"] == 0 and len(para["steps"]) == 2
        and para["steps"][0]["from"] == "X" and para["steps"][-1]["to"] == "X"
    )
    report("欧拉管网：零增程边界（空集唯一最优、零重复闭合回路）", ok)


# ---------- 3. 代码测试 ----------

def check_unittests():
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"), pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=1, stream=sys.stdout)
    ok = runner.run(suite).wasSuccessful()
    report("代码测试（tests/ 全套 unittest）", ok)


# ---------- 4. 构建完整性 ----------

def check_build():
    pyfiles = list((ROOT / "app").rglob("*.py"))
    try:
        for f in pyfiles:
            py_compile.compile(str(f), doraise=True)
        import app.server  # noqa: F401
        import app.validate  # noqa: F401
        import app.cpp  # noqa: F401
        page = ROOT / "app" / "static" / "index.html"
        assert page.is_file() and page.stat().st_size > 1000, "页面文件缺失"
        report("构建完整性（源码编译 + 应用模块导入 + 页面就位）", True,
               f"{len(pyfiles)} 个 Python 源文件 + index.html")
    except py_compile.PyCompileError as exc:
        report("构建完整性（源码编译 + 应用模块导入）", False, str(exc))


# ---------- 5. HTTP 冒烟 ----------

def _http(method, url, payload=None, timeout=5):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read())


def check_http():
    base = os.environ.get("WEB_URL")
    if not base:
        port = os.environ.get("PORT", "8000")
        base = f"http://127.0.0.1:{port}"
    try:
        status, body = _http("GET", base + "/healthz")
        assert status == 200 and body["status"] == "ok"
        payload = {
            "nodes": ["A", "B", "C"],
            "edges": [
                {"id": "e1", "u": "A", "v": "B", "length": 3},
                {"id": "e2", "u": "B", "v": "C", "length": 4},
            ],
            "depot": "A",
        }
        status, data = _http("POST", base + "/api/audit", payload)
        assert status == 200 and data["result"]["added_length"] == 7
        # 非法输入应被拒绝并可定位
        bad = dict(payload)
        bad = json.loads(json.dumps(bad))
        bad["edges"][0]["length"] = 0
        try:
            _http("POST", base + "/api/audit", bad)
            raise AssertionDataError("非法长度应返回错误码")
        except urllib.error.HTTPError as e:
            err = json.loads(e.read())
            assert e.code == 422 and err["error"]["code"] == "bad_length"
            assert err["error"]["loc"] == {"row": 0, "field": "length"}
        report(f"HTTP 冒烟（{base} 健康检查 / 审计成功 / 422 定位）", True)
    except Exception as exc:  # noqa: BLE001 - 冒烟失败统一报告
        report(f"HTTP 冒烟（{base}）", False, f"{type(exc).__name__}: {exc}")


class AssertionDataError(AssertionError):
    pass


def main():
    print("=" * 68)
    print("verify：中国邮递员审计服务单次校验")
    print("=" * 68, flush=True)
    check_odd_graph()
    check_eulerian_boundary()
    check_unittests()
    check_build()
    check_http()
    print("-" * 68)
    failed = [r for r in results if r[1] == FAIL]
    if failed:
        print(f"verify 结论：{len(failed)}/{len(results)} 项失败")
        return 1
    print(f"verify 结论：全部 {len(results)} 项通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""输入校验与 HTTP 接口测试。"""

import json
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

from app.cpp import AuditError
from app.server import Handler
from app.validate import validate


def base_payload():
    return {
        "nodes": ["A", "B", "C"],
        "edges": [
            {"id": "e1", "u": "A", "v": "B", "length": 3},
            {"id": "e2", "u": "B", "v": "C", "length": 4},
        ],
        "depot": "A",
    }


class ValidationTests(unittest.TestCase):
    def expect_error(self, payload, code, loc_row=None, loc_field=None):
        with self.assertRaises(AuditError) as ctx:
            validate(payload)
        self.assertEqual(ctx.exception.code, code)
        if loc_row is not None:
            self.assertEqual(ctx.exception.loc["row"], loc_row)
        if loc_field is not None:
            self.assertEqual(ctx.exception.loc["field"], loc_field)
        return ctx.exception

    def test_valid(self):
        nodes, edges, depot = validate(base_payload())
        self.assertEqual(nodes, ["A", "B", "C"])
        self.assertEqual(depot, "A")
        self.assertEqual(len(edges), 2)

    def test_node_count_bounds(self):
        p = base_payload()
        p["nodes"] = ["A"]
        self.expect_error(p, "node_count")
        p["nodes"] = [f"N{i}" for i in range(19)]
        self.expect_error(p, "node_count")

    def test_node_empty_duplicate_non_ascii(self):
        p = base_payload()
        p["nodes"] = ["A", " "]
        self.expect_error(p, "node_empty", 1)
        p["nodes"] = ["A", "节点"]
        self.expect_error(p, "node_not_ascii", 1)
        p["nodes"] = ["A", "A", "B"]
        self.expect_error(p, "node_duplicate", 1)

    def test_edge_count_bounds(self):
        p = base_payload()
        p["edges"] = []
        self.expect_error(p, "edge_count")

    def test_edge_id_empty_duplicate(self):
        p = base_payload()
        p["edges"][1]["id"] = "e1"
        self.expect_error(p, "edge_id_duplicate", 1, "id")
        p["edges"][0]["id"] = "  "
        self.expect_error(p, "edge_id_empty", 0, "id")

    def test_unknown_endpoint(self):
        p = base_payload()
        p["edges"][0]["u"] = "X"
        self.expect_error(p, "unknown_endpoint", 0, "u")
        p = base_payload()
        p["edges"][1]["v"] = "X"
        self.expect_error(p, "unknown_endpoint", 1, "v")

    def test_self_loop(self):
        p = base_payload()
        p["edges"][0]["v"] = "A"
        self.expect_error(p, "self_loop", 0, "v")

    def test_parallel_allowed(self):
        p = base_payload()
        p["edges"].append({"id": "e3", "u": "A", "v": "B", "length": 2})
        validate(p)  # 不应抛错

    def test_bad_length(self):
        for bad in (0, -3, 2.5, "7", None, True):
            p = base_payload()
            p["edges"][0]["length"] = bad
            self.expect_error(p, "bad_length", 0, "length")

    def test_depot_missing_or_unknown(self):
        p = base_payload()
        p["depot"] = ""
        self.expect_error(p, "depot_missing")
        p["depot"] = "Z"
        self.expect_error(p, "depot_unknown", loc_field="depot")

    def test_disconnected(self):
        p = base_payload()
        p["nodes"] = ["A", "B", "C", "D"]
        # e1,e2 连通 A-B-C；D 孤立
        self.expect_error(p, "disconnected")


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path):
        with urllib.request.urlopen(self.url(path), timeout=5) as resp:
            return resp.status, resp.read()

    def post(self, payload):
        req = urllib.request.Request(
            self.url("/api/audit"),
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_healthz(self):
        status, body = self.get("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "ok")

    def test_index(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("巡检".encode(), body)

    def test_audit_success(self):
        status, data = self.post(base_payload())
        self.assertEqual(status, 200)
        r = data["result"]
        self.assertEqual(r["added_length"], 7)
        self.assertEqual(r["canonical_duplicates"], ["e1", "e2"])
        self.assertEqual(r["steps"][0]["from"], "A")
        self.assertEqual(r["steps"][-1]["to"], "A")

    def test_audit_validation_error_status_and_loc(self):
        p = base_payload()
        p["edges"][0]["length"] = -1
        status, data = self.post(p)
        self.assertEqual(status, 422)
        self.assertEqual(data["error"]["code"], "bad_length")
        self.assertEqual(data["error"]["loc"], {"row": 0, "field": "length"})

    def test_bad_json(self):
        req = urllib.request.Request(
            self.url("/api/audit"), data=b"not json",
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            self.fail("应返回 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)


if __name__ == "__main__":
    unittest.main()

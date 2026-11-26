#!/usr/bin/env python3
"""独立 MCP 客户端探针：真实启动服务端子进程并完成一次握手、列工具、调工具。

只使用标准库，用于证明服务端能被外部客户端（而不是自身 demo 分支）正常驱动。
退出码：0 全部通过；2 握手失败；3 工具数量不符；4 检索结果与 CLI 不一致。
"""

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
QUERY = "混合检索 重排"


def fail(message: str, code: int) -> int:
    print(f"FAIL: {message}")
    return code


def rpc(proc, payload: dict) -> dict:
    proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError("服务端未返回任何内容（进程可能已退出）")
    return json.loads(line)


def main() -> int:
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.mcp_server"],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        init = rpc(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "probe", "version": "0.1"}},
        })
        if "error" in init:
            return fail(f"initialize 返回错误：{init['error']}", 2)
        result = init.get("result", {})
        print(f"protocolVersion: {result.get('protocolVersion')}")
        print(f"serverInfo: {result.get('serverInfo')}")

        # 通知不需要响应，服务端对无 id 的请求不回复
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()

        listing = rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = listing.get("result", {}).get("tools", [])
        names = [tool["name"] for tool in tools]
        print(f"tools({len(names)}): {names}")
        if len(names) != 3:
            return fail(f"工具数量预期 3，实际 {len(names)}", 3)

        missing = [tool for tool in tools if "inputSchema" not in tool]
        if missing:
            return fail(f"工具缺少 inputSchema：{[tool['name'] for tool in missing]}", 3)

        call = rpc(proc, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "kb_search", "arguments": {"query": QUERY, "limit": 3}},
        })
        payload = call.get("result", {})
        if not payload.get("ok"):
            return fail(f"kb_search 调用失败：{payload}", 4)
        ids = [row["doc_id"] for row in payload["result"]["results"]]
        detail_keys = all("score_detail" in row for row in payload["result"]["results"])
        print(f"kb_search top3: {ids}")
        print(f"score_detail 完整：{detail_keys}")
        if not detail_keys:
            return fail("检索结果缺少 score_detail", 4)

        cli = subprocess.run(
            [sys.executable, "-m", "src.kb.cli", "search", QUERY],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True,
        )
        raw = cli.stdout
        start = raw.find("[")
        if start < 0:
            return fail(f"CLI 未输出 JSON 数组：{raw[:120]}", 4)
        cli_rows, _ = json.JSONDecoder().raw_decode(raw[start:])
        cli_ids = [row["doc_id"] for row in cli_rows][:3]
        print(f"CLI top3:          {cli_ids}")
        if ids != cli_ids:
            return fail("服务端检索结果与 CLI 不一致", 4)

        print("PASS: 握手、工具列表、工具调用与 CLI 一致性全部通过")
        return 0
    except (RuntimeError, json.JSONDecodeError, OSError) as exc:
        return fail(str(exc), 2)
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())

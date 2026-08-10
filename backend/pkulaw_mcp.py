from __future__ import annotations

import json
import os
from dataclasses import dataclass
from itertools import count
from typing import Any

import requests
from dotenv import load_dotenv


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))


PKULAW_ENDPOINTS = {
    "law_semantic": "https://apim-gateway.pkulaw.com/mcp-law-search-service",
    "law_keyword": "https://apim-gateway.pkulaw.com/mcp-law",
    "case_semantic": "https://apim-gateway.pkulaw.com/mcp-case-search-service",
    "case_keyword": "https://apim-gateway.pkulaw.com/mcp-case",
    "law_item_keyword": "https://apim-gateway.pkulaw.com/mcp-fatiao",
    "law_recognition": "https://apim-gateway.pkulaw.com/law_recognition",
    "case_number_recognition": "https://apim-gateway.pkulaw.com/case_number_recognition",
    "citation_validator": "https://apim-gateway.pkulaw.com/pku_citation_validator",
    "doc_link": "https://apim-gateway.pkulaw.com/add-doc-link",
    "law_aggregate": "https://apim-gateway.pkulaw.com/mcp-law-agg/mcp",
}


class PkuLawMCPError(RuntimeError):
    pass


@dataclass
class MCPResponse:
    body: dict[str, Any]
    session_id: str = ""


class PkuLawMCPClient:
    """Minimal Streamable HTTP MCP client for the official Pkulaw gateway.

    The token is only read from the server environment and is never returned by
    API responses or written to logs/cache files.
    """

    def __init__(self, token: str | None = None, timeout: float = 35.0):
        raw = (token if token is not None else os.getenv("PKULAW_ACCESS_TOKEN", "")).strip()
        self.token = raw.removeprefix("Bearer ").strip()
        self.timeout = timeout
        self._ids = count(1)
        self._sessions: dict[str, str] = {}
        self._tools: dict[str, list[dict[str, Any]]] = {}

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "provider": "北大法宝 MCP",
            "portal": "https://mcp.pkulaw.com/",
            "services": sorted(PKULAW_ENDPOINTS),
        }

    def list_tools(self, service: str) -> list[dict[str, Any]]:
        if service in self._tools:
            return self._tools[service]
        endpoint = self._endpoint(service)
        session_id = self._initialize(service, endpoint)
        response = self._rpc(endpoint, "tools/list", {}, session_id=session_id)
        tools = response.body.get("result", {}).get("tools", [])
        if not isinstance(tools, list):
            raise PkuLawMCPError("北大法宝 MCP 返回了无法识别的工具清单")
        self._tools[service] = tools
        return tools

    def call_tool(self, service: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        endpoint = self._endpoint(service)
        session_id = self._sessions.get(service) or self._initialize(service, endpoint)
        response = self._rpc(
            endpoint,
            "tools/call",
            {"name": tool_name, "arguments": arguments},
            session_id=session_id,
        )
        return response.body.get("result", response.body)

    def search_cases(self, query: str, *, mode: str = "semantic", limit: int = 10) -> dict[str, Any]:
        service = "case_keyword" if mode == "keyword" else "case_semantic"
        tools = self.list_tools(service)
        if not tools:
            raise PkuLawMCPError("北大法宝案例 MCP 未公布可调用工具")
        tool = self._select_search_tool(tools)
        schema = tool.get("inputSchema") or tool.get("input_schema") or {}
        arguments = self._build_search_arguments(schema, query, limit)
        return self.call_tool(service, str(tool["name"]), arguments)

    def _initialize(self, service: str, endpoint: str) -> str:
        if not self.configured:
            raise PkuLawMCPError(
                "尚未配置北大法宝 Token。请在法宝 MCP 控制台获取后设置 PKULAW_ACCESS_TOKEN。"
            )
        response = self._rpc(
            endpoint,
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "risk-knowledge-map", "version": "1.0.0"},
            },
        )
        session_id = response.session_id
        if session_id:
            self._sessions[service] = session_id
        self._notify(endpoint, "notifications/initialized", {}, session_id=session_id)
        return session_id

    def _rpc(
        self,
        endpoint: str,
        method: str,
        params: dict[str, Any],
        *,
        session_id: str = "",
    ) -> MCPResponse:
        payload = {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params}
        response = requests.post(
            endpoint,
            headers=self._headers(session_id),
            json=payload,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            message = response.text[:300].strip() or response.reason
            raise PkuLawMCPError(f"北大法宝 MCP 请求失败（{response.status_code}）：{message}")
        body = self._decode_body(response)
        if "error" in body:
            error = body.get("error") or {}
            raise PkuLawMCPError(str(error.get("message") or error))
        return MCPResponse(body=body, session_id=response.headers.get("Mcp-Session-Id", ""))

    def _notify(
        self,
        endpoint: str,
        method: str,
        params: dict[str, Any],
        *,
        session_id: str = "",
    ) -> None:
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        response = requests.post(
            endpoint,
            headers=self._headers(session_id),
            json=payload,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise PkuLawMCPError(f"北大法宝 MCP 初始化确认失败（{response.status_code}）")

    def _headers(self, session_id: str = "") -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        return headers

    @staticmethod
    def _decode_body(response: requests.Response) -> dict[str, Any]:
        content_type = response.headers.get("content-type", "")
        text = response.text.strip()
        if not text:
            return {}
        if "text/event-stream" in content_type or text.startswith("data:"):
            events = []
            for line in text.splitlines():
                if line.startswith("data:"):
                    raw = line[5:].strip()
                    if raw and raw != "[DONE]":
                        events.append(json.loads(raw))
            if not events:
                return {}
            return events[-1]
        data = response.json()
        if not isinstance(data, dict):
            raise PkuLawMCPError("北大法宝 MCP 返回值不是 JSON 对象")
        return data

    @staticmethod
    def _select_search_tool(tools: list[dict[str, Any]]) -> dict[str, Any]:
        def score(tool: dict[str, Any]) -> int:
            text = f"{tool.get('name', '')} {tool.get('description', '')}".lower()
            return sum(
                weight
                for word, weight in (("search", 6), ("检索", 6), ("case", 4), ("案例", 4), ("query", 2))
                if word in text
            )

        return max(tools, key=score)

    @staticmethod
    def _build_search_arguments(schema: dict[str, Any], query: str, limit: int) -> dict[str, Any]:
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        arguments: dict[str, Any] = {}
        query_keys = ("query", "keyword", "keywords", "text", "q", "search_text", "question")
        limit_keys = ("limit", "size", "page_size", "pageSize", "top_k", "topK", "k")
        for key in query_keys:
            if key in properties:
                arguments[key] = query
                break
        else:
            arguments[query_keys[0]] = query
        for key in limit_keys:
            if key in properties:
                arguments[key] = max(1, min(int(limit), 50))
                break
        return arguments

    @staticmethod
    def _endpoint(service: str) -> str:
        try:
            return PKULAW_ENDPOINTS[service]
        except KeyError as exc:
            raise PkuLawMCPError(f"未知北大法宝 MCP 服务：{service}") from exc

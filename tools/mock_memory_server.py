"""模拟记忆服务（给故事平台同事开发用，不需要机器人）。

真正的记忆来自机器人对话。机器人还没接上时，用这个脚本"假装聊过天"：
  1. 启动：python3 mock_memory_server.py        （只用 Python 自带库，端口 8900）
  2. 假装机器人聊了一句：
     curl -X POST http://127.0.0.1:8900/events -d '{"source":"robot","type":"said","summary":"今天主人在公司被老板当众批评了，回家时很累、很低落。"}'
  3. 故事生成下一章前请求：GET http://127.0.0.1:8900/context?for=story
     → 返回刚才那句"机器人那边的新事"，取过一次就算已读，再请求就不会重复给。
故事里的选择也照样 POST /events（source=story），这里只打印出来，方便核对。
启动时已经预置了一条"机器人对话"，不做第 2 步也能直接测。
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

EVENTS = [{"source": "robot", "type": "said", "summary": "今天主人在公司被老板当众批评了，回家时很累、很低落。"}]
PROMISES = ["约定：周末一起去看海"]
READ_UPTO = {"story": 0, "robot": 0}


def context_for(side):
    other = "robot" if side == "story" else "story"
    new = [e for e in EVENTS[READ_UPTO[side]:] if e["source"] == other]
    READ_UPTO[side] = len(EVENTS)
    title = "现实中刚发生的事" if side == "story" else "故事里刚发生的事"
    lines = [f"【{title}】"] + [f"- {e['summary']}" for e in new]
    lines += ["【一直记得的】"] + [f"- {p}" for p in PROMISES]
    lines.append("请让小屿先回应主人的情绪，并自然提起这些事。" if new else "没有新的事，不要重复提旧事。")
    return {"has_new": bool(new), "text": "\n".join(lines)}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/context":
            side = parse_qs(url.query).get("for", ["story"])[0]
            return self._send(200, context_for(side if side in READ_UPTO else "story"))
        if url.path == "/all":
            return self._send(200, {"events": EVENTS, "promises": PROMISES})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if urlparse(self.path).path != "/events":
            return self._send(404, {"error": "not found"})
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        EVENTS.append(data)
        print("收到事件：", json.dumps(data, ensure_ascii=False), flush=True)
        self._send(200, {"ok": True, "id": f"evt_{len(EVENTS)}"})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print("模拟记忆服务已启动：http://127.0.0.1:8900", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8900), Handler).serve_forever()

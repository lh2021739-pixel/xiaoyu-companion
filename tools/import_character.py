"""把一个角色文件（人设 + 经历）导入记忆库，并设为机器人的当前角色。只用 Python 自带库。
用法：python3 tools/import_character.py tools/sample_characters/wife_test.json [--no-activate]
故事平台的"一键导出"做好后，也可以直接 POST 到 http://127.0.0.1:8900/characters/import，内容格式和这个文件一样。
"""
import json
import sys
import urllib.request

if len(sys.argv) < 2:
    sys.exit(__doc__)
data = json.load(open(sys.argv[1], encoding="utf-8"))
data["activate"] = "--no-activate" not in sys.argv
req = urllib.request.Request("http://127.0.0.1:8900/characters/import", json.dumps(data).encode(),
                             {"Content-Type": "application/json"})
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
print(json.loads(opener.open(req, timeout=10).read()))

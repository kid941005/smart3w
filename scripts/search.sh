#!/bin/bash
# searxng_search - 网页搜索脚本
# 用法: ./search.sh <关键词> [结果数量]

QUERY="$1"
COUNT="${2:-10}"
INSTANCE="${SEARXNG_INSTANCE:-https://searxng.example.com:59826}"

if [ -z "$QUERY" ]; then
    echo "用法: $0 <关键词> [结果数量]" >&2
    exit 1
fi

python3 - "$QUERY" "$COUNT" "$INSTANCE" << 'PYEOF'
import sys
import json
import urllib.request
import urllib.parse
import ssl

query = sys.argv[1]
count = int(sys.argv[2]) if len(sys.argv) > 2 else 10
instance = sys.argv[3] if len(sys.argv) > 3 else "https://searxng.example.com:59826"

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

params = {"q": query, "format": "json", "language": "zh-CN"}
url = f"{instance}/search?{urllib.parse.urlencode(params)}"

try:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        results = []
        for r in data.get("results", [])[:count]:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", "")
            })
        print(json.dumps({
            "success": True, "query": query,
            "results": results, "result_count": len(results)
        }, ensure_ascii=False, indent=2))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e), "query": query}))
PYEOF

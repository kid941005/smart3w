#!/bin/bash
# smart-fetch 智能抓取脚本
# 用法: ./fetch.sh <URL> [output_file]

URL="$1"
OUTPUT="${2:-/tmp/fetch_result.md}"

echo "=== Smart Fetch 开始 ==="
echo "目标: $URL"
echo "输出: $OUTPUT"
echo ""

# 步骤1: 尝试 scrapling extract get
echo "[1/2] 尝试 HTTP 抓取..."
scrapling extract get "$URL" "$OUTPUT" 2>/dev/null

if [ -s "$OUTPUT" ]; then
    echo "✅ HTTP 抓取成功"
    echo "内容长度: $(wc -c < "$OUTPUT") bytes"
    exit 0
fi

# 步骤2: 尝试 stealthy-fetch (绕过反爬)
echo "[2/2] 尝试隐身浏览器抓取..."
scrapling extract stealthy-fetch "$URL" "$OUTPUT" --headless 2>/dev/null

if [ -s "$OUTPUT" ]; then
    echo "✅ 隐身抓取成功"
    echo "内容长度: $(wc -c < "$OUTPUT") bytes"
    exit 0
fi

echo "❌ 所有方式都失败了"
exit 1

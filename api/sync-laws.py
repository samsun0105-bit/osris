from http.server import BaseHTTPRequestHandler
import json
import os
import time
import requests
import xml.etree.ElementTree as ET

# 取得專案根目錄
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
LAW_LIST_PATH = os.path.join(BASE_DIR, "law_list.json")

# 抓取單一法規的最新公布日
def get_law_date(pcode):
    url = f"https://law.moj.gov.tw/Service/GetOneLaw.aspx?PCode={pcode}"

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        root = ET.fromstring(response.content)
        node = root.find(".//LawModifiedDate")

        if node is not None and node.text:
            d = node.text.strip()
            return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"

    except Exception as e:
        return None

    return None


# Vercel API Handler
class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            # 讀取法規清單
            with open(LAW_LIST_PATH, "r", encoding="utf-8") as f:
                law_list = json.load(f)

            results = []

            # 逐筆抓取法規最新日期
            for law in law_list:
                latest = get_law_date(law["pcode"])

                results.append({
                    "Pcode": law["pcode"],
                    "Name": law["name"],
                    "LastUpdate": latest
                })

                time.sleep(0.2)  # 避免過快請求

            # 回傳 JSON
            body = json.dumps({
                "success": True,
                "count": len(results),
                "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                "data": results
            }, ensure_ascii=False).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        except Exception as e:
            body = json.dumps({
                "success": False,
                "error": str(e)
            }, ensure_ascii=False).encode("utf-8")

            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

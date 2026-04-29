from http.server import BaseHTTPRequestHandler
import json
import os
import time
import requests
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
LAW_LIST_PATH = os.path.join(BASE_DIR, "law_list.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


def get_law_date(pcode):
    url = f"https://law.moj.gov.tw/Service/GetOneLaw.aspx?PCode={pcode}"

    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()

        root = ET.fromstring(response.content)
        node = root.find(".//LawModifiedDate")

        if node is not None and node.text:
            d = node.text.strip()
            return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"

    except Exception:
        return None

    return None


def fetch_one_law(law):
    latest = get_law_date(law["pcode"])

    return {
        "Pcode": law["pcode"],
        "Name": law["name"],
        "LastUpdate": latest
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            with open(LAW_LIST_PATH, "r", encoding="utf-8") as f:
                law_list = json.load(f)

            results = []

            # 同時最多抓 5 筆，速度較快，也避免對外部網站請求太密集
            with ThreadPoolExecutor(max_workers=5) as executor:
                future_map = {
                    executor.submit(fetch_one_law, law): law
                    for law in law_list
                }

                for future in as_completed(future_map):
                    result = future.result()
                    results.append(result)

            # 依照 law_list 原始順序排序，避免畫面資料順序混亂
            order_map = {
                law["pcode"]: index
                for index, law in enumerate(law_list)
            }

            results.sort(
                key=lambda item: order_map.get(item["Pcode"], 9999)
            )

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
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

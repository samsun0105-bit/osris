from http.server import BaseHTTPRequestHandler
import json
import os
import re
import time
import requests
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BASE_DIR = os.path.dirname(os.path.dirname(__file__))
LAW_LIST_PATH = os.path.join(BASE_DIR, "law_list.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/149.0.0.0 Safari/537.36"
    ),
    "Accept": "application/xml,text/xml,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache"
}


def create_session():
    session = requests.Session()

    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )

    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=8,
        pool_maxsize=8
    )

    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session


def strip_namespace(tag):
    """
    移除 XML namespace。

    例如：
    {http://example.com}LawModifiedDate

    轉成：
    LawModifiedDate
    """
    if "}" in tag:
        return tag.split("}", 1)[1]

    return tag


def find_xml_text(root, possible_names):
    """
    不受 XML namespace 影響，
    尋找可能的日期欄位名稱。
    """
    target_names = {
        str(name).lower()
        for name in possible_names
    }

    for element in root.iter():
        element_name = strip_namespace(
            str(element.tag)
        ).lower()

        if element_name in target_names:
            text = (element.text or "").strip()

            if text:
                return text

    return None


def normalize_law_date(raw_date):
    """
    將全國法規資料庫可能回傳的日期格式，
    統一轉成 YYYY-MM-DD。

    支援：
    20260626
    2026-06-26
    2026/06/26
    1150626
    115-06-26
    民國115年6月26日
    中華民國115年6月26日
    """
    if raw_date is None:
        return None

    value = str(raw_date).strip()

    if not value:
        return None

    value = (
        value
        .replace("中華民國", "")
        .replace("民國", "")
        .replace("年", "-")
        .replace("月", "-")
        .replace("日", "")
        .replace("/", "-")
        .replace(".", "-")
        .strip()
    )

    digits = re.sub(r"\D", "", value)

    try:
        # 西元純數字：20260626
        if len(digits) == 8:
            year = int(digits[0:4])
            month = int(digits[4:6])
            day = int(digits[6:8])

        # 民國純數字：1150626
        elif len(digits) == 7:
            year = int(digits[0:3]) + 1911
            month = int(digits[3:5])
            day = int(digits[5:7])

        else:
            parts = [
                part
                for part in re.split(r"[-\s]+", value)
                if part
            ]

            if len(parts) < 3:
                return None

            year = int(parts[0])
            month = int(parts[1])
            day = int(parts[2])

            # 民國年轉西元年
            if year < 1911:
                year += 1911

        if year < 1912 or year > 2200:
            return None

        if month < 1 or month > 12:
            return None

        if day < 1 or day > 31:
            return None

        return f"{year:04d}-{month:02d}-{day:02d}"

    except (TypeError, ValueError):
        return None


def get_law_date(pcode):
    url = "https://law.moj.gov.tw/Service/GetOneLaw.aspx"
    session = create_session()

    try:
        response = session.get(
            url,
            params={
                "PCode": pcode,
                "_": int(time.time() * 1000)
            },
            headers=HEADERS,
            timeout=(8, 20)
        )

        response.raise_for_status()

        response_body = response.content

        if not response_body:
            raise ValueError(
                "全國法規資料庫回傳空白內容"
            )

        content_type = (
            response.headers
            .get("Content-Type", "")
            .lower()
        )

        body_preview = response_body[:500].decode(
            response.encoding or "utf-8",
            errors="ignore"
        ).lower()

        # 防止網站回傳錯誤 HTML 頁面
        if (
            "text/html" in content_type
            or "<!doctype html" in body_preview
            or "<html" in body_preview
        ):
            raise ValueError(
                "全國法規資料庫回傳 HTML，而不是法規 XML"
            )

        root = ET.fromstring(response_body)

        raw_date = find_xml_text(
            root,
            [
                "LawModifiedDate",
                "LawModifyDate",
                "ModifiedDate",
                "LawDate"
            ]
        )

        if not raw_date:
            raise ValueError(
                "XML 中找不到法規修正日期欄位"
            )

        normalized_date = normalize_law_date(raw_date)

        if not normalized_date:
            raise ValueError(
                f"無法辨識法規日期格式：{raw_date}"
            )

        return {
            "date": normalized_date,
            "rawDate": raw_date,
            "error": None
        }

    except requests.Timeout:
        return {
            "date": None,
            "rawDate": None,
            "error": "連線逾時"
        }

    except requests.RequestException as exc:
        return {
            "date": None,
            "rawDate": None,
            "error": f"HTTP 請求失敗：{str(exc)}"
        }

    except ET.ParseError as exc:
        return {
            "date": None,
            "rawDate": None,
            "error": f"XML 解析失敗：{str(exc)}"
        }

    except Exception as exc:
        return {
            "date": None,
            "rawDate": None,
            "error": str(exc)
        }

    finally:
        session.close()


def fetch_one_law(law):
    pcode = str(
        law.get("pcode", "")
    ).strip().upper()

    name = str(
        law.get("name", "")
    ).strip()

    if not pcode:
        return {
            "Pcode": "",
            "Name": name,
            "LastUpdate": None,
            "RawDate": None,
            "Success": False,
            "Error": "law_list.json 缺少 pcode"
        }

    fetch_result = get_law_date(pcode)

    return {
        "Pcode": pcode,
        "Name": name,
        "LastUpdate": fetch_result["date"],
        "RawDate": fetch_result["rawDate"],
        "Success": bool(fetch_result["date"]),
        "Error": fetch_result["error"]
    }


class handler(BaseHTTPRequestHandler):
    def send_json(self, status_code, payload):
        body = json.dumps(
            payload,
            ensure_ascii=False
        ).encode("utf-8")

        self.send_response(status_code)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )

        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, OPTIONS"
        )

        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type"
        )

        # 避免瀏覽器或平台使用舊同步資料
        self.send_header(
            "Cache-Control",
            "no-store, no-cache, must-revalidate, max-age=0"
        )

        self.send_header(
            "Pragma",
            "no-cache"
        )

        self.send_header(
            "Expires",
            "0"
        )

        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)

        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, OPTIONS"
        )

        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type"
        )

        self.end_headers()

    def do_GET(self):
        started_at = time.time()

        try:
            if not os.path.exists(LAW_LIST_PATH):
                raise FileNotFoundError(
                    f"找不到法規清單檔案：{LAW_LIST_PATH}"
                )

            with open(
                LAW_LIST_PATH,
                "r",
                encoding="utf-8"
            ) as file:
                law_list = json.load(file)

            if not isinstance(law_list, list):
                raise ValueError(
                    "law_list.json 最外層必須是陣列"
                )

            results = []

            # 同時最多三筆，避免請求過密或被限制
            with ThreadPoolExecutor(
                max_workers=3
            ) as executor:

                future_map = {
                    executor.submit(
                        fetch_one_law,
                        law
                    ): law
                    for law in law_list
                }

                for future in as_completed(future_map):
                    source_law = future_map[future]

                    try:
                        result = future.result()

                    except Exception as exc:
                        result = {
                            "Pcode": source_law.get(
                                "pcode",
                                ""
                            ),
                            "Name": source_law.get(
                                "name",
                                ""
                            ),
                            "LastUpdate": None,
                            "RawDate": None,
                            "Success": False,
                            "Error": (
                                "工作執行失敗："
                                f"{str(exc)}"
                            )
                        }

                    results.append(result)

            # 依 law_list.json 原始順序排列
            order_map = {
                str(
                    law.get("pcode", "")
                ).strip().upper(): index
                for index, law in enumerate(law_list)
            }

            results.sort(
                key=lambda item: order_map.get(
                    item["Pcode"],
                    999999
                )
            )

            success_count = sum(
                1
                for item in results
                if item["Success"]
            )

            failed_count = (
                len(results) - success_count
            )

            failed_items = [
                {
                    "Pcode": item["Pcode"],
                    "Name": item["Name"],
                    "Error": item["Error"]
                }
                for item in results
                if not item["Success"]
            ]

            self.send_json(
                200,
                {
                    "success": success_count > 0,
                    "count": len(results),
                    "successCount": success_count,
                    "failedCount": failed_count,
                    "updatedAt": time.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "elapsedSeconds": round(
                        time.time() - started_at,
                        2
                    ),
                    "data": results,
                    "failedItems": failed_items
                }
            )

        except Exception as exc:
            self.send_json(
                500,
                {
                    "success": False,
                    "count": 0,
                    "successCount": 0,
                    "failedCount": 0,
                    "error": str(exc),
                    "data": [],
                    "failedItems": []
                }
            )

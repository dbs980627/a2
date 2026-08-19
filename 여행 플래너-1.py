import os
import json
import argparse
from pathlib import Path

import requests
from dotenv import load_dotenv


load_dotenv()


def clean_json_block(text: str) -> str:
    """Gemini 응답에서 ```json ... ``` 블록을 제거하고 JSON 부분만 추출"""
    text = text.strip()

    if text.startswith("```json"):
        text = text[len("```json"):].strip()
    elif text.startswith("```"):
        text = text[len("```"):].strip()

    if text.endswith("```"):
        text = text[:-3].strip()

    # 혹시 앞뒤로 설명이 붙어 있으면 첫 { ~ 마지막 }만 추출
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and start < end:
        text = text[start:end + 1]

    return text


def get_fallback_recommendation(date_str: str) -> dict:
    """Gemini 실패 시 사용할 기본 추천"""
    return {
        "date": date_str,
        "destination": "서울",
        "schedule": [
            "10:00 - 경복궁 관람",
            "12:30 - 인사동에서 점심 식사",
            "14:30 - 북촌한옥마을 산책",
            "17:00 - 한강공원에서 여유롭게 마무리"
        ],
        "food_keyword": "서울 맛집"
    }


def generate_trip_with_gemini(date_str: str) -> dict:
    """Gemini API로 당일 여행 추천 생성"""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY가 .env에 설정되지 않았습니다.")

    model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    model = model.replace("models/", "")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    print(f"[DEBUG] 사용 모델: {model}")

    prompt = f"""
날짜: {date_str}
서울 출발 기준의 당일 여행 일정을 추천해줘.

반드시 아래 JSON 형식만 출력하고, 다른 설명은 절대 붙이지 마:
{{
  "destination": "여행지",
  "schedule": [
    "09:00 - 일정1",
    "13:00 - 일정2",
    "17:00 - 일정3"
  ],
  "food_keyword": "맛집 검색 키워드"
}}
""".strip()

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ]
    }

    response = requests.post(url, json=payload, timeout=30)
    print("[DEBUG] Gemini status:", response.status_code)
    print("[DEBUG] Gemini body:", response.text[:500])

    response.raise_for_status()

    data = response.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    text = clean_json_block(text)

    recommendation = json.loads(text)

    # 누락 값 보정
    if "date" not in recommendation:
        recommendation["date"] = date_str
    if "destination" not in recommendation:
        recommendation["destination"] = "서울"
    if "schedule" not in recommendation or not isinstance(recommendation["schedule"], list):
        recommendation["schedule"] = [
            "10:00 - 추천 일정 정보 없음",
            "13:00 - 추천 일정 정보 없음",
            "17:00 - 추천 일정 정보 없음"
        ]
    if "food_keyword" not in recommendation:
        recommendation["food_keyword"] = f'{recommendation["destination"]} 맛집'

    return recommendation


def search_kakao_places(keyword: str, size: int = 5) -> list:
    """Kakao Local API로 맛집 검색"""
    rest_api_key = os.getenv("KAKAO_REST_API_KEY")
    if not rest_api_key:
        print("[WARN] KAKAO_REST_API_KEY가 .env에 없습니다.")
        return []

    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {
        "Authorization": f"KakaoAK {rest_api_key}"
    }
    params = {
        "query": keyword,
        "size": size,
        "sort": "accuracy"
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)

        if response.status_code == 403:
            print("[WARN] Kakao 403 오류: REST API 키 또는 권한을 확인하세요.")
            print("[DEBUG] Kakao body:", response.text[:300])
            return []

        print("[DEBUG] Kakao status:", response.status_code)
        print("[DEBUG] Kakao body:", response.text[:300])

        response.raise_for_status()
        data = response.json()

        places = []
        for doc in data.get("documents", []):
            places.append({
                "name": doc.get("place_name", ""),
                "category": doc.get("category_name", ""),
                "address": doc.get("address_name", ""),
                "road_address": doc.get("road_address_name", ""),
                "phone": doc.get("phone", ""),
                "place_url": doc.get("place_url", ""),
                "x": doc.get("x", ""),
                "y": doc.get("y", "")
            })

        return places

    except requests.RequestException as e:
        print(f"[WARN] Kakao API 호출 실패: {e}")
        return []


def create_markdown_report(date_str: str, recommendation: dict, places: list) -> str:
    """Markdown 리포트 생성"""
    destination = recommendation.get("destination", "미정")
    schedule = recommendation.get("schedule", [])
    food_keyword = recommendation.get("food_keyword", "맛집")

    lines = [
        f"# 여행 리포트 ({date_str})",
        "",
        f"## 추천 여행지",
        f"- **목적지:** {destination}",
        "",
        "## 추천 일정"
    ]

    for item in schedule:
        lines.append(f"- {item}")

    lines.extend([
        "",
        "## 맛집 검색 키워드",
        f"- {food_keyword}",
        "",
        "## 맛집 검색 결과"
    ])

    if places:
        for idx, place in enumerate(places, start=1):
            lines.append(f"### {idx}. {place['name']}")
            if place["category"]:
                lines.append(f"- 분류: {place['category']}")
            if place["road_address"]:
                lines.append(f"- 도로명 주소: {place['road_address']}")
            if place["address"]:
                lines.append(f"- 지번 주소: {place['address']}")
            if place["phone"]:
                lines.append(f"- 전화번호: {place['phone']}")
            if place["place_url"]:
                lines.append(f"- 링크: {place['place_url']}")
            lines.append("")
    else:
        lines.append("- 검색 결과가 없습니다.")
        lines.append("")

    return "\n".join(lines)


def save_results(date_str: str, recommendation: dict, places: list, markdown_text: str):
    """JSON / Markdown 저장"""
    result_dir = Path("results")
    result_dir.mkdir(exist_ok=True)

    raw_path = result_dir / f"{date_str}_raw.json"
    md_path = result_dir / f"{date_str}_report.md"

    raw_data = {
        "date": date_str,
        "recommendation": recommendation,
        "places": places
    }

    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw_data, f, ensure_ascii=False, indent=2)

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown_text)

    print("[LOG] 결과 저장 완료")
    print(f"[LOG] JSON 파일: {raw_path}")
    print(f"[LOG] Markdown 파일: {md_path}")


def main():
    parser = argparse.ArgumentParser(description="Gemini 기반 여행 플래너")
    parser.add_argument("-date", required=True, help="여행 날짜 (예: 2025-08-20)")
    args = parser.parse_args()

    date_str = args.date

    print("[LOG] 프로그램 실행 시작")
    print(f"[LOG] 입력 날짜: {date_str}")

    try:
        print("[LOG] Gemini로 1차 여행 추천 생성 중...")
        recommendation = generate_trip_with_gemini(date_str)
        print("[LOG] Gemini 추천 생성 완료")

    except Exception as e:
        print(f"[ERROR] Gemini 호출 실패: {e}")
        print("[LOG] 기본 추천값으로 대체합니다.")
        recommendation = get_fallback_recommendation(date_str)

    food_keyword = recommendation.get("food_keyword", "서울 맛집")

    print(f"[LOG] Kakao 맛집 검색 중... ({food_keyword})")
    places = search_kakao_places(food_keyword)
    print(f"[LOG] 맛집 검색 완료: {len(places)}건")

    print("[LOG] Markdown 리포트 생성 중...")
    markdown_text = create_markdown_report(date_str, recommendation, places)

    save_results(date_str, recommendation, places, markdown_text)

    print("[LOG] 프로그램 종료")


if __name__ == "__main__":
    main()
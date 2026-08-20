import os
import json
import argparse
from datetime import datetime

import requests


# =========================
# 환경 변수 이름
# =========================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")


# =========================
# 지명 정규화 사전
# =========================
REGION_ALIASES = {
    "서울": "서울특별시",
    "서울시": "서울특별시",
    "부산": "부산광역시",
    "부산시": "부산광역시",
    "대구": "대구광역시",
    "대구시": "대구광역시",
    "인천": "인천광역시",
    "인천시": "인천광역시",
    "광주": "광주광역시",
    "광주시": "광주광역시",
    "대전": "대전광역시",
    "대전시": "대전광역시",
    "울산": "울산광역시",
    "울산시": "울산광역시",
    "세종": "세종특별자치시",
    "세종시": "세종특별자치시",
    "제주": "제주특별자치도",
    "제주도": "제주특별자치도"
}


def normalize_region(name: str) -> str:
    """지역명을 표준 행정명으로 정규화"""
    if not name:
        return name

    cleaned = name.strip()
    return REGION_ALIASES.get(cleaned, cleaned)


def normalize_region_in_text(text: str) -> str:
    """문장/검색어 안에 포함된 지역명을 정규화"""
    if not text:
        return text

    normalized = text.strip()

    for alias, official in sorted(REGION_ALIASES.items(), key=lambda x: len(x[0]), reverse=True):
        normalized = normalized.replace(alias, official)

    return normalized


# =========================
# 공통 유틸 함수
# =========================
def validate_date(date_str: str) -> str:
    """YYYY-MM-DD 형식 검증"""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return date_str
    except ValueError:
        raise argparse.ArgumentTypeError("날짜 형식은 YYYY-MM-DD 이어야 합니다. 예: 2025-08-20")


def extract_json_text(raw_text: str) -> str:
    """Gemini 응답의 코드블록 제거"""
    text = raw_text.strip()

    if text.startswith("```json"):
        text = text[7:].strip()
    elif text.startswith("```"):
        text = text[3:].strip()

    if text.endswith("```"):
        text = text[:-3].strip()

    return text


def create_fallback_recommendation(date_str: str, error_message: str = "") -> dict:
    """
    API 실패 시에도 보고서가 생성되도록 기본 추천 데이터 반환
    지역은 서울로 고정, 음식점은 없음 처리
    """
    reason = "외부 API 연동에 실패하여 기본 여행 보고서를 생성했습니다."
    if error_message:
        reason += f" (오류: {error_message})"

    return {
        "destination": "서울",
        "reason": reason,
        "schedule": [
            f"{date_str} 오전: 서울 시내 산책",
            f"{date_str} 오후: 서울 주요 명소 방문",
            f"{date_str} 저녁: 자유 일정"
        ],
        "food_keyword": "없음"
    }


# =========================
# Gemini 여행 추천 생성
# =========================
def generate_trip_with_gemini(date_str: str, errors=None) -> dict:
    if not GEMINI_API_KEY:
        raise ValueError("환경 변수 GEMINI_API_KEY가 설정되지 않았습니다.")

    if errors is None:
        errors = []

    error_text = ""
    if errors:
        error_text = "\n참고할 오류/제약사항:\n- " + "\n- ".join(errors)

    prompt = f"""
너는 여행 플래너다.
사용자가 여행 날짜를 입력하면 국내 여행지 1곳과 간단한 추천 이유, 일정, 맛집 검색 키워드를 JSON으로만 출력해라.

조건:
- 날짜: {date_str}
- 한국어로 작성
- 반드시 JSON만 출력
- 마크다운 사용 금지
- key는 아래 형식 그대로 사용

반드시 아래 형식으로 출력:
{{
  "destination": "여행지명",
  "reason": "추천 이유",
  "schedule": [
    "오전 일정",
    "오후 일정",
    "저녁 일정"
  ],
  "food_keyword": "지역명 맛집"
}}
{error_text}
""".strip()

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"

    headers = {
        "Content-Type": "application/json"
    }

    data = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.7
        }
    }

    response = requests.post(url, headers=headers, json=data, timeout=30)
    response.raise_for_status()
    result = response.json()

    try:
        raw_text = result["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise ValueError("Gemini 응답 형식을 해석할 수 없습니다.")

    cleaned = extract_json_text(raw_text)

    try:
        recommendation = json.loads(cleaned)
    except json.JSONDecodeError:
        raise ValueError(f"Gemini가 올바른 JSON을 반환하지 않았습니다.\n응답 내용:\n{raw_text}")

    # 기본값 보정
    if "destination" not in recommendation or not recommendation["destination"]:
        recommendation["destination"] = "서울"

    if "reason" not in recommendation or not recommendation["reason"]:
        recommendation["reason"] = "날짜에 어울리는 여행지로 추천되었습니다."

    if "schedule" not in recommendation or not isinstance(recommendation["schedule"], list):
        recommendation["schedule"] = ["오전 일정 추천", "오후 일정 추천", "저녁 일정 추천"]

    if "food_keyword" in recommendation and recommendation["food_keyword"]:
        recommendation["food_keyword"] = normalize_region_in_text(recommendation["food_keyword"])
    else:
        recommendation["food_keyword"] = f'{recommendation["destination"]} 맛집'

    # 목적지 정규화
    recommendation["destination"] = normalize_region(recommendation["destination"])

    return recommendation


# =========================
# Kakao 장소 검색
# =========================
def search_kakao_places(keyword: str, size: int = 5) -> list:
    if not KAKAO_REST_API_KEY:
        raise ValueError("환경 변수 KAKAO_REST_API_KEY가 설정되지 않았습니다.")

    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {
        "Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"
    }
    params = {
        "query": keyword,
        "size": size,
        "sort": "accuracy"
    }

    response = requests.get(url, headers=headers, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()

    return data.get("documents", [])


# =========================
# 출력 함수
# =========================
def print_recommendation(recommendation: dict, places: list) -> None:
    print("\n=== 여행 추천 결과 ===")
    print(f"여행지: {recommendation.get('destination', '서울')}")
    print(f"추천 이유: {recommendation.get('reason', '정보 없음')}")

    print("\n[추천 일정]")
    for idx, item in enumerate(recommendation.get("schedule", []), start=1):
        print(f"{idx}. {item}")

    food_keyword = recommendation.get("food_keyword", "없음")
    print(f"\n맛집 검색 키워드: {food_keyword}")

    print("\n[맛집 검색 결과]")
    if not places:
        print("없음")
        return

    for idx, place in enumerate(places, start=1):
        name = place.get("place_name", "이름 없음")
        address = place.get("road_address_name") or place.get("address_name", "주소 없음")
        phone = place.get("phone", "전화번호 없음")
        category = place.get("category_name", "분류 없음")
        place_url = place.get("place_url", "")

        print(f"{idx}. {name}")
        print(f"   - 주소: {address}")
        print(f"   - 전화: {phone}")
        print(f"   - 분류: {category}")
        if place_url:
            print(f"   - 링크: {place_url}")


# =========================
# 메인 함수
# =========================
def main():
    parser = argparse.ArgumentParser(description="날짜를 입력하면 여행지를 추천하는 프로그램")
    parser.add_argument("-date", required=True, type=validate_date, help='여행 날짜 (예: "2025-08-20")')
    args = parser.parse_args()

    date_str = args.date

    # 기본값: 실패해도 보고서는 무조건 생성
    recommendation = create_fallback_recommendation(date_str)
    places = []

    try:
        # 1. Gemini 추천 시도
        recommendation = generate_trip_with_gemini(date_str)

        # 2. Kakao 맛집 검색 시도
        try:
            food_keyword = recommendation.get("food_keyword", "")
            if food_keyword and food_keyword != "없음":
                places = search_kakao_places(food_keyword, size=5)
            else:
                places = []
        except Exception as e:
            # Kakao 실패 시: 보고서는 유지, 서울/음식점 없음으로 고정
            recommendation = create_fallback_recommendation(date_str, str(e))
            places = []

    except Exception as e:
        # Gemini 실패 시: 서울 기본 보고서 생성
        recommendation = create_fallback_recommendation(date_str, str(e))
        places = []

    print_recommendation(recommendation, places)


if __name__ == "__main__":
    main()
import os
import sys
import json
import argparse
from pathlib import Path

import requests
from dotenv import load_dotenv


load_dotenv()


# ---------------------------------------------------------------------------
# 공통 유틸
# ---------------------------------------------------------------------------

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


def print_gemini_key_missing_guide():
    """GEMINI_API_KEY 미설정 시 안내 메시지 출력"""
    print("[ERROR] GEMINI_API_KEY가 설정되어 있지 않습니다.")
    print("[안내] 프로그램을 실행하려면 Gemini API 키가 반드시 필요합니다.")
    print("[안내] 아래 순서로 설정해주세요.")
    print("  1) https://aistudio.google.com/ 에서 Gemini API 키를 발급받습니다.")
    print("  2) 이 스크립트와 같은 폴더에 '.env' 파일을 만듭니다.")
    print("  3) .env 파일에 다음 줄을 추가합니다.")
    print("     GEMINI_API_KEY=발급받은_키_값")
    print("  4) (선택) 사용할 모델을 지정하려면 아래 줄도 추가합니다.")
    print("     GEMINI_MODEL=gemini-3.6-flash")


def add_error(errors: list, stage: str, message: str):
    """오류 목록에 항목 추가 + 콘솔 로그"""
    errors.append({"stage": stage, "message": message})
    print(f"[ERROR] ({stage}) {message}")


# ---------------------------------------------------------------------------
# Gemini 여행 추천
# ---------------------------------------------------------------------------

def build_trip_prompt(date_str: str, strict: bool = False) -> str:
    """Gemini에 보낼 프롬프트 생성.
    strict=True 인 경우, 파싱 실패로 인한 재시도 상황이므로
    반드시 필요한 최소 키만 다시 JSON으로 출력하도록 강하게 지시한다.
    """
    if not strict:
        return f"""
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

    # 재시도용 프롬프트: 형식을 더 단순하고 엄격하게 강제
    return f"""
이전 응답이 JSON으로 파싱되지 않았다.
날짜: {date_str}
서울 출발 기준의 당일 여행 일정을 아래 3개의 키만 사용해서
"오직 JSON 객체 하나만" 다시 출력해라.
설명, 코드블록 표시(```), 줄바꿈 문구, 그 외 어떤 텍스트도 절대 포함하지 마라.

{{"destination": "여행지", "schedule": ["09:00 - 일정1", "13:00 - 일정2", "17:00 - 일정3"], "food_keyword": "맛집 검색 키워드"}}
""".strip()


def call_gemini_api(url: str, prompt: str) -> str:
    """Gemini API를 한 번 호출하고 텍스트 응답을 반환한다.
    네트워크 오류/HTTP 오류/응답 구조 오류는 모두 예외로 전파한다.
    """
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
    return text


def generate_trip_with_gemini(date_str: str, errors: list) -> dict:
    """Gemini API로 당일 여행 추천 생성.

    - API 호출/네트워크 오류: 예외를 그대로 상위로 전파해 main()에서
      fallback 추천으로 대체하도록 한다.
    - JSON 파싱 실패: 더 엄격한 프롬프트로 1회 재시도한다.
      재시도까지 실패하면 예외를 전파해 fallback으로 대체한다.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    model = model.replace("models/", "")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    print(f"[DEBUG] 사용 모델: {model}")

    # 1차 호출
    try:
        text = call_gemini_api(url, build_trip_prompt(date_str, strict=False))
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        add_error(errors, "gemini_call", f"Gemini API 호출 실패: {e}")
        raise

    cleaned = clean_json_block(text)

    try:
        recommendation = json.loads(cleaned)
    except json.JSONDecodeError as e:
        add_error(errors, "gemini_json_parse", f"1차 응답 JSON 파싱 실패: {e}")
        print("[LOG] JSON 파싱 실패 - 필수 키만 다시 출력하도록 1회 재요청합니다.")

        # 2차(재시도) 호출
        try:
            text_retry = call_gemini_api(url, build_trip_prompt(date_str, strict=True))
            cleaned_retry = clean_json_block(text_retry)
            recommendation = json.loads(cleaned_retry)
            print("[LOG] 재시도 성공 - JSON 파싱 완료")
        except (requests.RequestException, KeyError, IndexError, ValueError, json.JSONDecodeError) as e2:
            add_error(errors, "gemini_json_parse_retry", f"재시도 후에도 JSON 파싱 실패: {e2}")
            raise

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


# ---------------------------------------------------------------------------
# Kakao 맛집 검색
# ---------------------------------------------------------------------------

def search_kakao_places(keyword: str, errors: list, size: int = 5) -> list:
    """Kakao Local API로 맛집 검색.

    네트워크/인증/쿼터 등 어떤 이유로든 실패하면 빈 리스트를 반환하고
    오류를 errors 목록에 기록한다. 이 함수의 실패는 프로그램 전체를
    중단시키지 않고, 리포트에는 '검색 결과 없음'으로 표시된다.
    """
    rest_api_key = os.getenv("KAKAO_REST_API_KEY")
    if not rest_api_key:
        add_error(errors, "kakao_search", "KAKAO_REST_API_KEY가 .env에 설정되지 않았습니다.")
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
        print("[DEBUG] Kakao status:", response.status_code)
        print("[DEBUG] Kakao body:", response.text[:300])

        if response.status_code == 401 or response.status_code == 403:
            add_error(
                errors,
                "kakao_search",
                f"Kakao 인증 오류(status={response.status_code}): REST API 키 또는 권한을 확인하세요."
            )
            return []

        if response.status_code == 429:
            add_error(errors, "kakao_search", "Kakao API 쿼터(요청 한도) 초과로 검색에 실패했습니다.")
            return []

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
        add_error(errors, "kakao_search", f"Kakao API 네트워크 오류: {e}")
        return []
    except (ValueError, KeyError) as e:
        add_error(errors, "kakao_search", f"Kakao 응답 처리 오류: {e}")
        return []


# ---------------------------------------------------------------------------
# 리포트 생성 및 저장
# ---------------------------------------------------------------------------

def create_markdown_report(date_str: str, recommendation: dict, places: list, errors: list) -> str:
    """Markdown 리포트 생성 (오류 요약 섹션 포함)"""
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
        lines.append("- 데이터 없음 (맛집 검색에 실패했거나 결과가 없습니다.)")
        lines.append("")

    lines.append("## 오류 요약 (errors)")
    if errors:
        for err in errors:
            lines.append(f"- [{err['stage']}] {err['message']}")
    else:
        lines.append("- 오류 없음")
    lines.append("")

    return "\n".join(lines)


def save_results(date_str: str, recommendation: dict, places: list, markdown_text: str, errors: list):
    """JSON / Markdown 저장"""
    result_dir = Path("results")
    result_dir.mkdir(exist_ok=True)

    raw_path = result_dir / f"{date_str}_raw.json"
    md_path = result_dir / f"{date_str}_report.md"

    raw_data = {
        "date": date_str,
        "recommendation": recommendation,
        "places": places,
        "errors": errors
    }

    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw_data, f, ensure_ascii=False, indent=2)

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown_text)

    print("[LOG] 결과 저장 완료")
    print(f"[LOG] JSON 파일: {raw_path}")
    print(f"[LOG] Markdown 파일: {md_path}")


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Gemini 기반 여행 플래너")
    parser.add_argument("-date", required=True, help="여행 날짜 (예: 2025-08-20)")
    args = parser.parse_args()

    date_str = args.date
    errors = []

    print("[LOG] 프로그램 실행 시작")
    print(f"[LOG] 입력 날짜: {date_str}")

    # 정책 1: API 키(Gemini) 미설정 시 즉시 종료 + 안내
    if not os.getenv("GEMINI_API_KEY"):
        print_gemini_key_missing_guide()
        sys.exit(1)

    # 정책 3: LLM 호출/JSON 파싱 실패는 generate_trip_with_gemini 내부에서
    # 1회 재시도까지 처리하며, 그래도 실패하면 여기서 fallback으로 대체한다.
    try:
        print("[LOG] Gemini로 1차 여행 추천 생성 중...")
        recommendation = generate_trip_with_gemini(date_str, errors)
        print("[LOG] Gemini 추천 생성 완료")

    except Exception as e:
        add_error(errors, "gemini_fallback", f"Gemini 추천 생성 최종 실패, 기본값으로 대체: {e}")
        print("[LOG] 기본 추천값으로 대체합니다.")
        recommendation = get_fallback_recommendation(date_str)

    food_keyword = recommendation.get("food_keyword", "서울 맛집")

    # 정책 2: 맛집 검색 API 실패 시 '데이터 없음' 처리하고 계속 진행
    print(f"[LOG] Kakao 맛집 검색 중... ({food_keyword})")
    places = search_kakao_places(food_keyword, errors=errors)
    print(f"[LOG] 맛집 검색 완료: {len(places)}건")

    print("[LOG] Markdown 리포트 생성 중...")
    markdown_text = create_markdown_report(date_str, recommendation, places, errors)

    save_results(date_str, recommendation, places, markdown_text, errors)

    if errors:
        print(f"[LOG] 실행 중 {len(errors)}건의 오류가 있었습니다. 리포트의 '오류 요약' 섹션을 확인하세요.")

    print("[LOG] 프로그램 종료")


if __name__ == "__main__":
    main()

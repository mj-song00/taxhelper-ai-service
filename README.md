# 1. 프로젝트 소개 
TaxHelper는 국가법령정보센터의 법령 및 판례 데이터를 검색하고, 검색된 근거를 바탕으로 답변을 생성하는 RAG 기반 세법 질의응답 서비스입니다.

이 저장소는 사용자의 자연어 질문을 분석하고, Spring Boot 검색 API를 호출한 뒤 검색 결과를 재정렬하여 Ollama 기반 답변을 생성하는 FastAPI AI Service입니다.

# 2. 시스템 구성
```text
사용자 질문
    ↓
FastAPI AI Service
- 자연어 질문 분석
- 법령명·세목·핵심 키워드 추출
    ↓
Spring Boot Backend
- 법령·판례 검색
- 검색 후보 반환
    ↓
FastAPI AI Service
- 검색 결과 재정렬
- LLM 컨텍스트 구성
    ↓
Ollama
- 검색 근거 기반 답변 생성
    ↓
Streaming Response
```
# 3. 주요 기능 및 개발 상태 
> 현재 개발 중인 프로젝트입니다.

### 구현 완료
- 사용자 자연어 질문 분석
- 법령명·세목·핵심 키워드 추출
- Spring Boot 법령 검색 API 연동
- 검색 후보 점수 계산 및 재정렬
- 검색 근거 기반 프롬프트 생성
- Ollama API 연동
- 스트리밍 답변 반환
- 반복 질문 Redis 캐시
- 서버 시작 시 Ollama 웜업 
- 검색 및 LLM 처리 구간별 로그 기록

### 진행 중
- 판례 검색 결과 재정렬
- 자연어 질문과 법령 표현 간 검색 정확도 개선
- 법률 개념 커버리지 기반 점수 계산
- 검색 실패 감지 및 fallback 검색
- 답변 정확도 평가

### 개발 예정
- 법령과 판례를 함께 활용한 답변 생성
- 검색 결과 평가 데이터 구축
- 서비스 배포
- 일관된 말투로 응답하게 조정

# 4. 실행 환경
### 구성 요소
- Python 3.12
- FastAPI
- Spring Boot Backend
- Redis
- Ollama 0.30.7
- LLM Model: `qwen3:4b`

FastAPI AI Service는 사용자 질문을 처리하기 위해 Spring Boot Backend의 검색 API와 Ollama 실행 환경이 필요합니다.

### 환경변수 
```
SPRING_BASE_URL=http://localhost:8080

RETRIEVAL_OLLAMA_BASE_URL=http://localhost:11434
RETRIEVAL_OLLAMA_MODEL=qwen3:4b
RETRIEVAL_REQUEST_TIMEOUT_SEC=120
```

# 5. 실행 방법
### 실행 전 준비
전체 질의응답 기능을 사용하려면 다음 서비스가 먼저 실행되어 있어야 합니다.

1. PostgreSQL
2. Spring Boot Backend
3. Ollama
4. FastAPI AI Service

### 관련 저장소

- **FastAPI AI Service**: 현재 저장소
- **Spring Boot Backend**: [TaxHelper-backend](https://github.com/mj-song00/TaxHelper-backend)

### 1. 가상환경 생성 
```
cd app
source .venv/bin/activate     
```
### 2. 의존성 설치
```
pip install -r requirements.txt
```
### 3. FastAPI 실행
```
fastapi dev
(혹시 실행이 안되면 python -m fastapi dev )
```

# 6. 기술 스택
### Backend
- Python 3.12
- FastAPI
- Pydantic
- HTTPX
- Uvicorn

### LLM
- Ollama
- `qwen3:4b-instruct-2507-q4_K_M`

### API
- REST API
- SSE(Server-Sent Events) 스트리밍

# 7. 처리 흐름 
## 질의응답 처리 흐름
1. 사용자 질문을 입력받습니다.
2. 질문에서 법령명, 세목 및 핵심 법률 개념을 추출합니다.
3. 검색 조건을 생성하여 Spring Boot 검색 API를 호출합니다.
4. 반환된 법령·판례 후보를 질문과의 관련도에 따라 재정렬합니다.
5. 상위 검색 결과를 LLM 컨텍스트로 구성합니다.
6. Ollama에 검색 근거와 질문을 전달합니다.
7. 생성된 답변을 Streaming Response로 반환합니다.

# 8. 기술적 고민과 의사결정
### Spring Boot와 FastAPI 역할 분리
법령 데이터 관리와 AI 처리 로직은 사용하는 기술과 변경 주기가 다르다고 판단했습니다.<br>
Spring Boot는 법령·판례 데이터 수집, 관계형 데이터 저장 및 검색 API를 담당하고, FastAPI는 자연어 질문 분석, 검색 결과 재정렬 및 LLM 답변 생성을 담당하도록 역할을 분리했습니다.<br>
이를 통해 검색 데이터 관리와 AI 처리 로직을 독립적으로 수정하고 실험할 수 있도록 구성했습니다.

### 검색 근거 기반 답변 생성
LLM이 일반 지식을 이용해 근거 없는 답변을 생성하지 않도록 검색된 법령과 판례만 사용하도록 시스템 프롬프트를 구성했습니다.<br>
질문과 직접 관련된 근거가 없는 경우에는 판단할 수 없음을 명시하도록 제한하여 답변의 근거 추적 가능성을 높였습니다.<br>

# 9. 트러블 슈팅 : 긴 검색 근거로 인한 Ollama 컨텍스트 초과
<img width="1366" height="298" alt="image" src="https://github.com/user-attachments/assets/9e3b1a79-f6f1-406c-b687-5ee27a092790" />

### 문제
법령과 판례 검색 결과가 길어지면 Ollama에서 exceed_context_size_error가 발생하고 답변 생성이 실패했습니다.
<img width="1366" height="298" alt="image" src="https://github.com/user-attachments/assets/81e69579-0e95-4296-b4d5-4be5997081b6" />

### 원인
검색 결과의 원문 길이는 문자 단위로 관리했지만, Ollama의 컨텍스트 제한은 토큰 단위로 적용됩니다.
따라서 문자 수를 제한하더라도 질문, 시스템 프롬프트, 법령 및 판례를 합친 전체 토큰 수가 모델의 num_ctx를 초과할 수 있었습니다.
<img width="1307" height="39" alt="스크린샷 2026-07-22 오후 1 52 47" src="https://github.com/user-attachments/assets/6a4b037f-6910-4a7f-9601-e862bfd47f41" />

### 해결 
- LLM에 전달하는 검색 컨텍스트 길이 제한
- 문서 전체 대신 핵심 개념 밀집 구간 추출
- 스트리밍 오류 응답을 먼저 읽은 후 상태 코드 처리
- exceed_context_size_error 감지
- 실제 적용된 컨텍스트를 기준으로 60% 축소
- 축소한 컨텍스트로 한 번만 재시도
- 무한 재시도를 막기 위한 _context_retry 플래그 추가
- 재시도 전후 컨텍스트 길이 로깅

### 결과 
컨텍스트 초과 오류 발생 시 검색 근거를 60%로 축소하고 한 번만 재시도하도록 개선했습니다.<br>
재시도 여부와 축소 전후 컨텍스트 길이를 로그로 남겨 오류 원인을 확인할 수 있게 했으며, 동일한 오류가 무한 반복되지 않도록 재시도 횟수를 제한했습니다.

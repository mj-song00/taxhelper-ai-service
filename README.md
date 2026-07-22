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
JSON / SSE Response
- 답변 및 법령·판례 출처 반환
```
# 3. 주요 기능 및 개발 상태 
> 현재 개발 중인 프로젝트입니다.

### 구현 완료
- 사용자 자연어 질문 분석
- 법령명·세목·핵심 법률 개념 추출
- Spring Boot 법령·판례 검색 API 연동
- 법률 개념 커버리지 기반 검색 결과 재정렬
- 핵심 개념 밀집 구간을 이용한 LLM 컨텍스트 구성
- 기본·엄격·보충 검색을 병렬 실행하고 결과 통합
- 법령과 판례를 함께 활용한 Ollama 답변 생성
- 일반 JSON 및 SSE 스트리밍 응답 제공
- 답변에 사용된 법령·판례 출처 반환
- 동일 판례 출처 중복 제거
- 검색 결과 및 LLM 답변 인메모리 TTL 캐시
- 서버 시작 시 Ollama 워밍업
- 검색·모델 로드·첫 토큰·답변 생성 구간별 로깅

### 진행 중
- 자연어 질문과 법령 표현 사이의 검색 정확도 개선
- 대표 실패 질문 기반 회귀 테스트 확장
- 답변 정확도 및 검색 품질 평가 데이터 구축
- 답변 형식과 문체의 일관성 개선

### 개발 예정
- 서비스 배포
- 검색 및 답변 품질의 정량 평가 자동화
- 인메모리 캐시의 외부 캐시 전환 검토

# 4. 실행 환경
### 구성 요소
- Python 3.12
- FastAPI
- Spring Boot Backend
- Ollama 
- LLM Model: `qwen3:4b`

FastAPI AI Service는 사용자 질문을 처리하기 위해 Spring Boot Backend의 검색 API와 Ollama 실행 환경이 필요합니다.

### 환경변수 
```env
RETRIEVAL_SPRING_BASE_URL=http://localhost:8080

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


### Ollama 준비

로컬 환경에 Ollama가 설치되어 있어야 합니다.

사용할 모델을 내려받습니다.


```bash
ollama pull qwen3:4b

### 1. 저장소 복제

```bash
git clone -b ai-python https://github.com/mj-song00/taxhelper-ai-service.git
cd taxhelper-ai-service
```

### 2. 가상환경 생성
```
python -m venv .venv
```

### 3. 가상환경 활성화
```
source .venv/bin/activate     
```
### 4. 의존성 설치
```
pip install -r requirements.txt
```
### 5. FastAPI 실행
```bash
uvicorn app.main:app --reload
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
- Model: `qwen3:4b-instruct-2507-q4_K_M`
- Quantization: `Q4_K_M`

### API
- REST API
- SSE (Server-Sent Events) 스트리밍

# 7. 처리 흐름 
## 질의응답 처리 흐름
1. 사용자 질문을 입력받습니다.
2. 질문에서 법령명, 세목 및 핵심 법률 개념을 추출합니다.
3. 검색 조건을 생성하여 Spring Boot 검색 API를 호출합니다.
4. 반환된 법령·판례 후보를 질문과의 관련도에 따라 재정렬합니다.
5. 상위 검색 결과를 LLM 컨텍스트로 구성합니다.
6. Ollama에 검색 근거와 질문을 전달합니다.
7. 요청한 API에 따라 일반 JSON 또는 SSE 스트리밍으로 답변과 출처를 반환합니다.

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
검색 컨텍스트는 문자 수를 기준으로 제한했지만, Ollama의 컨텍스트 한도는 토큰 수를 기준으로 계산됩니다.<br> 
시스템 프롬프트, 사용자 질문, 법령 및 판례 근거를 모두 합친 토큰 수가 `num_ctx`를 초과하면 문자 수 제한을 통과했더라도 `exceed_context_size_error`가 발생하였습니다.
<img width="1307" height="39" alt="스크린샷 2026-07-22 오후 1 52 47" src="https://github.com/user-attachments/assets/6a4b037f-6910-4a7f-9601-e862bfd47f41" />

### 해결 
- LLM에 전달하는 검색 컨텍스트 길이 제한
- 문서 전체 대신 핵심 개념 밀집 구간 추출
- 스트리밍 오류 응답을 먼저 읽은 후 상태 코드 처리
- exceed_context_size_error 감지
- 실제 적용된 컨텍스트 길이를 기준으로 60%까지 축소하되, 최소 1,000자는 유지
- 축소한 컨텍스트로 한 번만 재시도
- 재시도 횟수를 제한하여 반복 요청 방지
- 재시도 전후 컨텍스트 길이 로깅

### 결과 
컨텍스트 초과 오류가 발생하면 최초 원문 길이가 아니라 실제 LLM 요청에 적용된 컨텍스트 길이를 기준으로 60%까지 축소하되, 최소 1,000자는 유지한 상태로 한 번만 재시도하도록 개선했습니다.<br>
재시도 여부와 축소 전후 컨텍스트 길이를 로그로 기록해 오류 원인을 추적할 수 있도록 했습니다. 또한 `_context_retry` 플래그로 재시도를 한 번으로 제한하여 동일한 오류가 반복되는 것을 방지했습니다.

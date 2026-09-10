# 1. 프로젝트 소개 
TaxHelper는 국가법령정보센터의 법령 및 판례 데이터를 검색하고, 검색된 근거를 바탕으로 답변을 생성하는 RAG 기반 세법 질의응답 서비스입니다.

이 저장소는 사용자의 자연어 질문을 분석하고, Spring Boot 검색 API를 호출해 검색 결과를 재정렬하며, 
RabbitMQ와 Worker를 이용해 Ollama 답변 생성을 비동기로 처리하는 FastAPI AI Service입니다.

# 2. 시스템 구성
```text
사용자 질문
    ↓
Spring Boot Backend
- 질의 작업 요청
    ↓
FastAPI AI Service
- 자연어 질문 분석
- 법령명·세목·핵심 키워드 추출
    ↓
Spring Boot Backend · PostgreSQL
- 법령·판례 검색
- 작업 정보와 검색 근거 저장
    ↓
FastAPI AI Service
- 검색 결과 재정렬
- LLM 컨텍스트 구성
- job_id를 RabbitMQ에 발행
    ↓
RabbitMQ
- LLM 작업 대기
    ↓
FastAPI Worker
- 작업 상태를 PROCESSING으로 변경
- Ollama 기반 답변 생성
- 답변 저장 완료 후 메시지 ack
    ↓
Spring Boot Backend · PostgreSQL
- COMPLETED/FAILED 상태와 결과 저장
    ↓
사용자
- Spring Boot 작업 상태 및 결과 저장 API 연동
```

# 3. 주요 기능 및 개발 상태 
> 핵심 기능 구현과 동시 요청 처리 구조 개선을 완료했으며, 추가 기능 개발은 보류 중입니다.

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
- RabbitMQ 기반 LLM 작업 비동기 처리
- `job_id` 기반 작업 발행 및 Worker 소비
- `PREPARING → WAITING → PROCESSING → COMPLETED/FAILED` 상태 관리
- Worker 1개와 `prefetch_count=1`을 이용한 순차 처리
- 답변과 작업 상태 저장 완료 후 RabbitMQ 메시지 수동 ack
- 작업 상태 및 완료 결과 조회

### 후속 개선 과제
- Worker 장애 시 메시지 재전달 및 작업 상태 복구 검증
- 중복 메시지 처리 방지

# 4. 실행 환경
### 구성 요소
- Python 3.12
- FastAPI
- Spring Boot Backend
- Ollama 
- LLM Model: `qwen3:4b`
- RabbitMQ
- FastAPI API Server
- FastAPI Worker

> 전체 질의응답 기능을 실행하려면 Spring Boot Backend, PostgreSQL, RabbitMQ, Ollama, FastAPI API Server와 Worker가 필요합니다.

### 환경변수 
```env
RETRIEVAL_SPRING_BASE_URL=http://localhost:8080

RETRIEVAL_OLLAMA_BASE_URL=http://localhost:11434
RETRIEVAL_OLLAMA_MODEL=qwen3:4b
RETRIEVAL_REQUEST_TIMEOUT_SEC=120

RABBITMQ_URL=amqp://guest:guest@localhost/
RABBITMQ_QUEUE=taxhelper.llm.jobs
```

# 5. 실행 방법
### 실행 전 준비
전체 질의응답 기능을 사용하려면 다음 서비스가 먼저 실행되어 있어야 합니다.

1. PostgreSQL 실행
2. Spring Boot Backend 실행
3. RabbitMQ 실행
4. Ollama 실행 및 모델 준비
5. FastAPI API Server 실행
6. FastAPI Worker 실행

### 관련 저장소

- **FastAPI AI Service**: 현재 저장소
- **Spring Boot Backend**: [TaxHelper-backend](https://github.com/mj-song00/TaxHelper-backend)


### Ollama 준비

로컬 환경에 Ollama가 설치되어 있어야 합니다.

사용할 모델을 내려받습니다.


```bash
ollama pull qwen3:4b
```

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
### 6. FastAPI Worker 실행
FastAPI API Server와 별도의 터미널에서 실행합니다.
```bash
python -m app.workers.rabbitmq_worker
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
- Base Model: `qwen3:4b-instruct-2507`
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
5. 상위 검색 결과로 LLM 컨텍스트를 구성하고 작업 정보를 저장합니다.
6. `job_id`를 RabbitMQ의 `taxhelper.llm.jobs` 큐에 발행합니다.
7. Worker가 메시지를 가져와 작업 상태를 `PROCESSING`으로 변경합니다.
8. Ollama에 질문과 검색 근거를 전달하여 답변을 생성합니다.
9. 답변과 `COMPLETED` 상태를 Spring Boot를 통해 PostgreSQL에 저장합니다.
10. 저장이 완료된 후 RabbitMQ 메시지를 ack합니다.
11. 사용자는 작업 조회 API를 통해 상태와 완료된 답변을 확인합니다.

# 8. 기술적 고민과 의사결정
### Spring Boot와 FastAPI 역할 분리
법령 데이터 관리와 AI 처리 로직은 사용하는 기술과 변경 주기가 다르다고 판단했습니다.<br>
Spring Boot는 법령·판례 데이터 수집, 관계형 데이터 저장 및 검색 API를 담당하고, FastAPI는 자연어 질문 분석, 검색 결과 재정렬 및 LLM 답변 생성을 담당하도록 역할을 분리했습니다.<br>
이를 통해 검색 데이터 관리와 AI 처리 로직을 독립적으로 수정하고 실험할 수 있도록 구성했습니다.

### Semaphore에서 RabbitMQ 기반 비동기 처리로 전환

로컬 Ollama의 동시 실행을 제한하기 위해 Semaphore를 적용했지만, 후속 요청이 HTTP 연결을 유지한 채 앞선 작업의 완료를 기다리면서 Spring Boot의 read timeout을 초과했습니다.

HTTP 요청과 LLM 실행을 분리하기 위해 API Server는 검색과 작업 등록 후 `job_id`를 RabbitMQ에 발행하고, 별도 Worker가 Ollama 호출과 결과 저장을 처리하도록 변경했습니다. 
로컬 Ollama의 처리 특성을 고려해 Worker 1개, `prefetch_count=1`, Semaphore 1을 유지했습니다.

이를 통해 LLM 처리시간 자체를 단축하지는 않았지만, 장시간 실행되는 LLM 작업이 HTTP 타임아웃으로 실패하지 않고 큐에서 대기한 뒤 처리되도록 구조를 변경했습니다.

> 전체 동시 요청 테스트와 측정 결과는 Spring Boot Backend README에서 확인할 수 있습니다.


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

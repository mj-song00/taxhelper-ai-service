# RabbitMQ 작업 복구

기존 Spring의 조건부 UPDATE와 Worker의 수동 ACK를 유지한다. 새 테이블이나 별도 복구 프로세스는 없다. 복구 작업은 각 Worker 안의 비동기 작업이며, 실제 상태 판정과 원자적 변경은 Spring/PostgreSQL이 수행한다.

## 실행과 상태 정책

- WAITING 선점 시 `attemptId`, `leaseExpiresAt`, `attemptCount`를 원자적으로 저장한다. 같은 회차의 선점 재요청은 횟수를 늘리지 않는다.
- 완료·실패·heartbeat는 현재 회차이고 lease가 유효한 PROCESSING에만 적용한다. 종료 상태의 기존 답변·오류·시각은 보존한다.
- 회차 없는 준비 단계 실패 요청은 PREPARING/WAITING에만 적용한다.
- lease 시각은 PostgreSQL UTC 시계를 사용한다. 내부 API는 UTC 오프셋을 포함한 문자열을 반환하므로 전역 ObjectMapper 설정에 의존하지 않는다.
- PROCESSING 메시지는 중복 실행 없이 ACK한다. 별도 DB 복구 작업이 만료 상태를 회수하므로 메시지가 이미 ACK되었거나 소실되어도 회수할 수 있다.
- 만료 작업은 WAITING으로 돌아가 다음 선점에서 새 회차를 받는다. 최대 실행 횟수를 소진하면 FAILED로 종료한다.
- WAITING의 `leaseExpiresAt`은 **재발행 억제 시각**이다. WAITING 선점은 이 시각과 무관하게 허용한다. 발행 실패나 복구 응답 유실 시에도 억제 시간이 지나면 다시 발행한다.
- `recover` API는 커서와 최대 100건 배치를 사용한다. 여러 복구 Worker가 경합해도 조건부 UPDATE를 통과한 요청만 발행 후보를 받는다.

## 콜백 오류와 DLQ

LLM 생성 실패는 실패 콜백으로 전달한다. 답변 생성 후 완료 저장 오류는 같은 답변의 저장만 재시도하며 실패 콜백으로 바꾸지 않는다. 콜백 재시도 소진 시 DB를 조회하여 종료 여부 또는 회차 변경을 확인한다.

처리할 수 없는 메시지는 DLQ로 직접 발행하고 publisher confirm 후 원본을 ACK한다. DLQ 발행이 실패하면 ACK/폐기하지 않고 예외가 전파되어 소비 연결이 닫히며 미ACK 메시지는 브로커에 남는다. 이 경우 Worker 프로세스가 종료될 수 있으므로 배포 환경의 프로세스 재시작 정책이 필요하다. 내부 복구 루프의 API/재발행 실패는 지연을 늘려 재시도한다.

DLQ에는 원본 메시지의 base64, jobId, attemptId, 오류 종류를 보관한다. DLQ 메시지를 자동으로 원래 큐에 되돌리지 않는다. DB 복구 작업은 별도로 실행되며, DLQ는 사고 분석 기록으로 남는다. DLQ의 보존 기간과 경보 정책은 운영 설정으로 정해야 한다.

## 기본 설정

Spring 속성:

| 속성 | 기본값 |
|---|---:|
| `taxhelper.jobs.lease-seconds` | 90 |
| `taxhelper.jobs.max-attempts` | 3 |
| `taxhelper.jobs.max-attempt-seconds` | 1800 |
| `taxhelper.jobs.redispatch-seconds` | 60 |

Worker 환경변수:

| 변수 | 기본값 |
|---|---:|
| `RETRIEVAL_JOB_API_ATTEMPTS` | 3 |
| `RETRIEVAL_JOB_API_TIMEOUT_SEC` | 5 |
| `RETRIEVAL_JOB_RETRY_DELAY_SEC` | 1 |
| `RETRIEVAL_JOB_HEARTBEAT_SEC` | 15 |
| `RETRIEVAL_JOB_MAX_RUNTIME_SEC` | 1800 |
| `RETRIEVAL_JOB_RECOVERY_INTERVAL_SEC` | 30 |
| `RETRIEVAL_JOB_RECOVERY_BATCH_SIZE` | 100 |
| `RABBITMQ_DLQ` | `taxhelper.llm.jobs.dlq` |

`RABBITMQ_URL`, `RABBITMQ_QUEUE`, `RETRIEVAL_SPRING_BASE_URL`은 기존 설정을 사용한다. 환경별 주 큐와 DLQ를 분리해야 한다. heartbeat 간격과 최악의 API 재시도 시간을 합해도 lease보다 충분히 짧도록 설정한다. 기본 재시도는 최초 요청을 포함한 3회이며 대기는 1초, 2초다. 반복 heartbeat를 무한 작업 허가로 쓰지 않도록 서버와 Worker의 실행 시간 제한을 함께 유지한다. 서버의 갱신 차단 이후 이미 발급된 lease가 만료될 때까지 짧은 여유가 생길 수 있다.

## 배포 순서

1. 구버전 Worker를 중지하고 새 작업 유입을 통제한다. 구버전과 신버전 Worker를 함께 실행하지 않는다.
2. 백엔드의 `src/main/resources/database/chat_job_lease_v2.sql`을 검토하여 대상 DB에 적용한다. 이번 자동 검증에서는 격리 DB에만 적용했다.
3. 새 Spring과 FastAPI/Worker 코드를 함께 배포한다. 선점·완료 콜백의 API 계약이 달라졌다.
4. 기존 PROCESSING의 실제 처리 주체가 없는지 확인한 뒤 새 Worker를 시작한다. NULL lease의 기존 PROCESSING도 복구 대상이다.
5. DLQ 접근 권한, 재발행 로그, 최대 횟수 실패, 프로세스 재시작 정책을 확인한다.

추가 컬럼은 기존 종료 행을 변경하지 않는다. SQL은 두 번 실행해도 안전하도록 작성되었으며, 적용 전후 기존 필드 비교를 격리 DB에서 검증했다.

## 보장 범위

정상 중복 전달에서는 한 번의 선점만 성공한다. 장애 복구 시 재추론은 허용하며, 이전 회차는 새로운 결과를 저장할 수 없다. 네트워크 단절 후 Ollama 서버 내부의 기존 추론까지 즉시 중단되는 것은 보장하지 않는다. 생성된 미저장 답변은 메모리에 있으므로 프로세스 종료 후 재생성이 필요할 수 있다.

복구를 위해서는 최소 한 Worker와 Spring/DB/브로커의 가용성이 필요하다. 모든 Worker가 중단되면 복구도 중단되고 재시작 후 재개된다. LLM 실행 횟수는 제한되지만, 선점 전 조회 장애나 메시지 발행 장애가 지속되는 WAITING 작업에는 전체 대기 수명 제한을 추가하지 않았다. PREPARING 상태 자동 복구와 브로커/DB 자체 장애 처리는 이번 구현의 검증 범위 밖이다.

## 검증 및 Git 포함 대상

Python `tests/test_rabbitmq_worker.py`는 실제 Worker 소스를 로드하고 외부 의존성만 Mock으로 대체한다. Java의 기존 ChatJob 서비스 테스트와 PostgreSQL 테스트는 새 회차 계약에 맞춰 확장했다. 실제 RabbitMQ/Spring/PostgreSQL/Worker 장애 재현에서는 Ollama HTTP 응답과 API 오류 지점만 제어했다.

이 저장소에서 포함할 파일은 `app/core/config.py`, `app/services/chat_job_client.py`, `app/services/rabbitmq_publisher.py`, `app/workers/rabbitmq_worker.py`, `tests/test_rabbitmq_worker.py`, 이 문서다. `.env`, 앱 로그, 부하테스트 결과와 로컬 장애 검증의 비밀번호 포함 설정 및 증거 파일은 함께 추가하지 않는다. 백엔드 변경은 별도 저장소의 ChatJob 소스·테스트·DB 변경 스크립트를 포함해야 한다.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

DROP DATABASE IF EXISTS texHelper;
CREATE DATABASE taxHelper;

CREATE TABLE users
(
    id         UUID PRIMARY KEY,
    createdAt TIMESTAMP(6),
    updatedAt TIMESTAMP(6),
    deletedAt TIMESTAMP(6),
    email      VARCHAR(255) NOT NULL,
    nickname  VARCHAR(255) NOT NULL,
    password   VARCHAR(255),
    userRole  VARCHAR(255) NOT NULL CHECK (userRole IN ('USER', 'ADMIN'))
);

CREATE TABLE laws--법령(기본정보 포함)
(
    id             BIGINT PRIMARY KEY,
    lawId          INT,       -- 법령 ID
    lawKey         INT,                       -- 법령키
    nameKor        VARCHAR(255) NOT NULL,     -- 법령명_한글
    nameHanja      VARCHAR(255),              -- 법령명_한자
    nameShort      VARCHAR(255),              -- 법령명약칭
    decisionType   VARCHAR(50),               -- 의결구분
    proposalType   VARCHAR(50),               -- 제안구분
    proclamationNo VARCHAR(50),               -- 공포번호
    proclamationDate VARCHAR(50),             -- 공포일자 (YYYYMMDD → DATE 변환)
    enforcementDate VARCHAR(50),              -- 시행일자 (YYYYMMDD → DATE 변환)
    phoneNumber    VARCHAR(50),               -- 전화번호
    language        VARCHAR(50),              -- 언어 (한글/영문 등)
    revisionType   VARCHAR(50),               -- 제개정구분
    revisionClassification VARCHAR(50),       -- 제개정구분
    joinMinistry  VARCHAR(255),               -- 공동부령정보
    isProclaimed   VARCHAR(50),               -- 공포법령여부 (Y/N → boolean)
    isKorean       VARCHAR(50),               -- 한글법령여부 (Y/N → boolean)
    isTitleChanged VARCHAR(50),               -- 제명변경여부 (Y/N → boolean)
    hasAnnex       VARCHAR(50),               -- 별표편집여부 (Y/N → boolean)
    structureCode  VARCHAR(50)                -- 편장절관 (법령 구조 코드)

);


CREATE TABLE articles -- 조문
(
    articleId       BIGSERIAL PRIMARY KEY,  -- 조문키
    lawId           BIGINT NOT NULL,        -- 법령ID (FK)
    articleNo       INT,                    -- 조문번호
    title            VARCHAR(255),          -- 조문제목
    content          TEXT,                  -- 조문내용
    enforcementDate VARCHAR(50),            -- 조문시행일자
    isChanged       VARCHAR(50),            -- 조문변경여부
    moveBefore      INT,                    -- 조문이동이전
    moveAfter       INT,                    -- 조문이동이후
    reference        TEXT,                  -- 조문참고자료
    articleType     VARCHAR(50),            -- 조문여부
    branchNo        INT                     -- 조문가지번호

);

CREATE TABLE paragraph -- 조문에 속한 항
(
    paragraphId  BIGSERIAL PRIMARY KEY,   -- 항목키
    paragraphNo  VARCHAR(50),                  -- 항번호
    revisionType       VARCHAR(50),               -- 항제개정유형
    revisionDateStr   VARCHAR(20),               -- 항제개정일자문자열 (원문 그대로 저장)
    content             TEXT,                      -- 항내용
    articleId  BIGSERIAL
);

CREATE TABLE item -- 항에 속한 호
(
    subItemId       BIGSERIAL PRIMARY KEY,                     -- 내부 PK (소스에 호키 없으므로 생성)
    subItemNo       VARCHAR(50),                              -- 호번호
    subContent       TEXT,   -- 호내용
    branchNo     VARCHAR(50), -- 호가지번호
    paragraphId  BIGSERIAL
);

CREATE TABLE subItem  -- 호에 속한 목 \
(
    subItemId BIGSERIAL PRIMARY KEY,
    subNO VARCHAR(255),   -- 목번호
    content VARCHAR(255)  -- 목번호

);


CREATE TABLE supplements -- 부칙
(
    supplementId     BIGSERIAL PRIMARY KEY,   -- 부칙키
    lawId            BIGINT NOT NULL,     -- 법령ID (FK: laws)
    proclamationNo   INT,              -- 부칙공포번호
    proclamationDate DATE,                     -- 부칙공포일자
    content           TEXT[]                    -- 부칙내용 (배열로 저장, 각 항목 한 줄)
);

CREATE TABLE amendments -- 개정문
(
    amendmentId   BIGSERIAL PRIMARY KEY,       -- 내부 PK
    lawId         BIGINT NOT NULL,    -- 법령ID (FK: laws)
    title          VARCHAR(255),            -- 개정문 제목 (예: 대통령령 제23987호 등)
    content        TEXT[]                 -- 개정문 내용 (배열로 저장, 각 줄을 요소로)
);

CREATE TABLE ministries(  -- 소관 부처
    ministryId      BIGSERIAL PRIMARY KEY ,     -- 소관부처 저장번호
    ministryName    VARCHAR(50),    -- 소관부처 이름
    ministryCode    VARCHAR(50)     -- 소관부처 코드
);

CREATE TABLE departmentUnit --부서단위
(
    deptId          BIGSERIAL PRIMARY KEY,               -- 부서키
    deptName       VARCHAR(100) NOT NULL,               -- 부서명
    deptPhone      VARCHAR(50),               -- 부서연락처
    ministryId     VARCHAR(20) NOT NULL      -- 소관부처 FK
);

CREATE TABLE law_types -- 법종 구분
(
    lawTypeId      BIGSERIAL PRIMARY KEY,
    lawType         VARCHAR(50),              -- "법률", "시행령" 등 법종 구문
    lawTypeCode    VARCHAR(20)                -- "A0002" 등 코드
);

-- Spring Batch 테이블
CREATE TABLE BATCH_JOB_INSTANCE
(
    job_instance_id BIGINT       NOT NULL PRIMARY KEY,
    version         BIGINT,
    job_name        VARCHAR(100) NOT NULL,
    job_key         VARCHAR(32)  NOT NULL,
    UNIQUE KEY uk_job_name_key (job_name, job_key)
);

CREATE TABLE BATCH_JOB_EXECUTION
(
    job_execution_id BIGINT      NOT NULL PRIMARY KEY,
    version          BIGINT,
    job_instance_id  BIGINT      NOT NULL,
    create_time      DATETIME(6) NOT NULL,
    start_time       DATETIME(6),
    end_time         DATETIME(6),
    status           VARCHAR(10),
    exit_code        VARCHAR(2500),
    exit_message     VARCHAR(2500),
    last_updated     DATETIME(6),
    FOREIGN KEY (job_instance_id) REFERENCES BATCH_JOB_INSTANCE (job_instance_id)
);

CREATE TABLE BATCH_JOB_EXECUTION_PARAMS
(
    job_execution_id BIGINT       NOT NULL,
    parameter_name   VARCHAR(100) NOT NULL,
    parameter_type   VARCHAR(100) NOT NULL,
    parameter_value  VARCHAR(2500),
    identifying      CHAR(1)      NOT NULL,
    FOREIGN KEY (job_execution_id) REFERENCES BATCH_JOB_EXECUTION (job_execution_id)
) ENGINE = InnoDB;

CREATE TABLE BATCH_STEP_EXECUTION
(
    step_execution_id  BIGINT       NOT NULL PRIMARY KEY,
    version            BIGINT       NOT NULL,
    step_name          VARCHAR(100) NOT NULL,
    job_execution_id   BIGINT       NOT NULL,
    create_time        DATETIME(6)  NOT NULL,
    start_time         DATETIME(6),
    end_time           DATETIME(6),
    status             VARCHAR(10),
    commit_count       BIGINT,
    read_count         BIGINT,
    filter_count       BIGINT,
    write_count        BIGINT,
    read_skip_count    BIGINT,
    write_skip_count   BIGINT,
    process_skip_count BIGINT,
    rollback_count     BIGINT,
    exit_code          VARCHAR(2500),
    exit_message       VARCHAR(2500),
    last_updated       DATETIME(6),
    FOREIGN KEY (job_execution_id) REFERENCES BATCH_JOB_EXECUTION (job_execution_id)
);

CREATE TABLE BATCH_STEP_EXECUTION_CONTEXT
(
    step_execution_id  BIGINT        NOT NULL PRIMARY KEY,
    short_context      VARCHAR(2500) NOT NULL,
    serialized_context TEXT,
    FOREIGN KEY (step_execution_id) REFERENCES BATCH_JOB_EXECUTION (job_execution_id)
);

CREATE TABLE BATCH_JOB_EXECUTION_CONTEXT
(
    job_execution_id   BIGINT        NOT NULL PRIMARY KEY,
    short_context      VARCHAR(2500) NOT NULL,
    serialized_context TEXT,
    FOREIGN KEY (job_execution_id) REFERENCES BATCH_JOB_EXECUTION (job_execution_id)
);

-- 커스텀 배치 테이블
CREATE TABLE BATCH_LOCK
(
    id        BIGINT       NOT NULL,
    job_name  VARCHAR(255) NOT NULL,
    locked_at DATETIME(6)  NOT NULL,
    locked_by VARCHAR(255) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_job_name (job_name)
);

CREATE TABLE BATCH_JOB_REQUEST
(
    id           BIGINT       NOT NULL,
    job_name     VARCHAR(255) NOT NULL,
    job_param    VARCHAR(255),
    requested_at DATETIME(6)  NOT NULL,
    PRIMARY KEY (id)
);

-- 시퀀스 테이블
CREATE TABLE BATCH_STEP_EXECUTION_SEQ
(
    id         BIGINT  NOT NULL,
    unique_key CHAR(1) NOT NULL,
    UNIQUE KEY uk_batch_step_seq (unique_key)
);

INSERT INTO BATCH_STEP_EXECUTION_SEQ (id, unique_key)
VALUES (0, '0')
ON DUPLICATE KEY UPDATE id = id;

CREATE TABLE BATCH_JOB_EXECUTION_SEQ
(
    id         BIGINT  NOT NULL,
    unique_key CHAR(1) NOT NULL,
    UNIQUE KEY uk_batch_job_exec_seq (unique_key)
);

INSERT INTO BATCH_JOB_EXECUTION_SEQ (id, unique_key)
VALUES (0, '0')
ON DUPLICATE KEY UPDATE id = id;

CREATE TABLE BATCH_JOB_SEQ
(
    id         BIGINT  NOT NULL,
    unique_key CHAR(1) NOT NULL,
    UNIQUE KEY uk_batch_job_seq (unique_key)
);

INSERT INTO BATCH_JOB_SEQ (id, unique_key)
VALUES (0, '0')
ON DUPLICATE KEY UPDATE id = id;
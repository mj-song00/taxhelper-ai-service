import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

const BASE_URL = (__ENV.BASE_URL || 'http://localhost:8080').replace(/\/$/, '');
const TOP_K = Number(__ENV.TOP_K || 3);
const POLL_INTERVAL_SECONDS = Number(__ENV.POLL_INTERVAL_SECONDS || 2);
const JOB_TIMEOUT = __ENV.JOB_TIMEOUT || '10m';
const VUS = Number(__ENV.VUS || 2);

const QUESTIONS = [
  '부가가치세 매입세액 공제를 받을 수 없는 경우는 무엇인가요?',
  '상속받은 부동산을 양도할 때 취득가액은 어떻게 계산하나요?',
];

const jobSubmitDuration = new Trend('job_submit_duration', true);
const jobSubmitFailureRate = new Rate('job_submit_failure_rate');
const jobTotalDuration = new Trend('job_total_duration', true);
const jobCompletedRate = new Rate('job_completed_rate');
const jobFailedRate = new Rate('job_failed_rate');
const pollingRequestDuration = new Trend('polling_request_duration', true);
const pollingRequests = new Counter('polling_requests');
const waitingObservations = new Counter('waiting_observations');
const processingObservations = new Counter('processing_observations');

export const options = {
  scenarios: {
    async_chat_jobs: {
      executor: 'per-vu-iterations',
      vus: VUS,
      iterations: 1,
      maxDuration: JOB_TIMEOUT,
    },
  },
  thresholds: {
    checks: ['rate>0.99'],
    job_submit_duration: ['p(95)<5000'],
    job_submit_failure_rate: ['rate<0.01'],
    job_completed_rate: ['rate>0.99'],
    job_failed_rate: ['rate<0.01'],
  },
  summaryTrendStats: ['avg', 'p(95)', 'max'],
};

function durationToMs(value) {
  const match = String(value).trim().match(/^([0-9]+(?:\.[0-9]+)?)\s*(ms|s|m|h)?$/i);
  if (!match) throw new Error(`Invalid JOB_TIMEOUT: ${value}`);
  const number = Number(match[1]);
  const unit = (match[2] || 'ms').toLowerCase();
  return number * ({ ms: 1, s: 1000, m: 60000, h: 3600000 }[unit]);
}

function jsonBody(response) {
  try {
    return response.json();
  } catch (_) {
    return {};
  }
}

export default function () {
  const jobStartedAt = Date.now();
  const question = QUESTIONS[(__VU - 1) % QUESTIONS.length];
  const timeoutMs = durationToMs(JOB_TIMEOUT);
  let failureMessage = null;
  let jobId = null;

  const submitResponse = http.post(
    `${BASE_URL}/api/ui/chat/jobs`,
    JSON.stringify({ question, topK: TOP_K }),
    {
      headers: { 'Content-Type': 'application/json' },
      tags: { endpoint: 'spring_chat_job_submit' },
      timeout: JOB_TIMEOUT,
    },
  );
  jobSubmitDuration.add(submitResponse.timings.duration);
  const submitPayload = jsonBody(submitResponse);
  const submitOk = check(submitResponse, {
    'job POST status is 202': (response) => response.status === 202,
    'job POST returns jobId': () => typeof submitPayload.jobId === 'string' && submitPayload.jobId.length > 0,
    'job POST status is WAITING': () => submitPayload.status === 'WAITING',
  });
  jobSubmitFailureRate.add(!submitOk);

  if (!submitOk) {
    failureMessage = `job submit failed status=${submitResponse.status}`;
  } else {
    jobId = submitPayload.jobId;
    while (Date.now() - jobStartedAt < timeoutMs) {
      sleep(POLL_INTERVAL_SECONDS);
      if (Date.now() - jobStartedAt >= timeoutMs) {
        failureMessage = 'job polling timeout';
        break;
      }

      const pollResponse = http.get(
        `${BASE_URL}/api/ui/chat/jobs/${encodeURIComponent(jobId)}`,
        { tags: { endpoint: 'spring_chat_job_poll' }, timeout: '30s' },
      );
      pollingRequestDuration.add(pollResponse.timings.duration);
      pollingRequests.add(1);
      const pollPayload = jsonBody(pollResponse);
      const pollOk = check(pollResponse, {
        'polling status is 200': (response) => response.status === 200,
      });
      if (!pollOk) {
        failureMessage = `polling failed status=${pollResponse.status}`;
        break;
      }

      if (pollPayload.status === 'PREPARING' || pollPayload.status === 'WAITING') {
        waitingObservations.add(1);
        continue;
      }
      if (pollPayload.status === 'PROCESSING') {
        processingObservations.add(1);
        continue;
      }
      if (pollPayload.status === 'COMPLETED') {
        break;
      }
      if (pollPayload.status === 'FAILED') {
        failureMessage = pollPayload.message || 'job failed';
        break;
      }
      failureMessage = `unexpected job status=${pollPayload.status}`;
      break;
    }

    if (!failureMessage && Date.now() - jobStartedAt >= timeoutMs) {
      failureMessage = 'job polling timeout';
    }
  }

  const completed = failureMessage === null;
  jobTotalDuration.add(Date.now() - jobStartedAt);
  jobCompletedRate.add(completed);
  jobFailedRate.add(!completed);

  if (failureMessage) {
    throw new Error(failureMessage);
  }
}

export function handleSummary(data) {
  const metric = (name) => data.metrics[name] && data.metrics[name].values;
  const values = (name) => metric(name) || {};
  const ms = (value) => (Number.isFinite(value) ? `${value.toFixed(2)} ms` : 'N/A');
  const percent = (value) => (Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : 'N/A');
  const report = [
    '',
    '=== TaxHelper RabbitMQ async k6 결과 ===',
    `Job 등록 평균/p95/최대  ${ms(values('job_submit_duration').avg)} / ${ms(values('job_submit_duration')['p(95)'])} / ${ms(values('job_submit_duration').max)}`,
    `Job 전체 평균/p95/최대    ${ms(values('job_total_duration').avg)} / ${ms(values('job_total_duration')['p(95)'])} / ${ms(values('job_total_duration').max)}`,
    `Job 등록 실패율           ${percent(values('job_submit_failure_rate').rate)}`,
    `Job 완료율                ${percent(values('job_completed_rate').rate)}`,
    `Job 실패율                ${percent(values('job_failed_rate').rate)}`,
    `Polling 요청 수           ${values('polling_requests').count || 0}`,
    `WAITING 관찰 횟수         ${values('waiting_observations').count || 0}`,
    `PROCESSING 관찰 횟수      ${values('processing_observations').count || 0}`,
    `Polling 평균/p95          ${ms(values('polling_request_duration').avg)} / ${ms(values('polling_request_duration')['p(95)'])}`,
    '',
  ].join('\n');
  return {
    stdout: report,
    [__ENV.SUMMARY_FILE || 'k6-summary-rabbitmq-2vu.json']: JSON.stringify(data, null, 2),
  };
}

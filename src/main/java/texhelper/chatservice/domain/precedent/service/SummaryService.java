package texhelper.chatservice.domain.precedent.service;

import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;
import texhelper.chatservice.domain.precedent.dto.request.SummaryDetailRequest;
import texhelper.chatservice.domain.precedent.dto.request.SummaryShell;
import texhelper.chatservice.domain.precedent.entity.PrecedentSummary;
import texhelper.chatservice.domain.precedent.repository.PrecedentSummaryRepository;

import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.Collections;
import java.util.List;
import java.util.Optional;

@Service
@Slf4j
public class SummaryService {

    public SummaryService(WebClient webClient, PrecedentSummaryRepository precedentSummaryRepository, PrecedentProperties precedentProperties) {
        this.webClient = webClient;
        this.precedentSummaryRepository = precedentSummaryRepository;
        this.precedentProperties = precedentProperties;
    }

    private final WebClient webClient;
    private final PrecedentSummaryRepository precedentSummaryRepository;
    private final PrecedentProperties precedentProperties;

    @Value("${law.api.email}")
    String email;


    public void fetchAllKeywords() {
        for (String keywords : precedentProperties.getKeywords()) {
            fetchAndSaveSummaries(keywords);
        }
    }

    public void fetchAndSaveSummaries(String keywords) {
        int page = 1;
        int totalPages = Integer.MAX_VALUE;
        DateTimeFormatter formatter = DateTimeFormatter.ofPattern("yyyy.MM.dd");
        LocalDate tenYearsAgo = LocalDate.now().minusYears(10);

        while (page <= totalPages) {
            SummaryShell request = webClient.get()
                    .uri(uriBuilder -> uriBuilder
                            .path("/lawSearch.do")
                            .queryParam("OC", email)
                            .queryParam("target", "prec")
                            .queryParam("type", "JSON")
                            .queryParam("query", keywords)
                            .build())
                    .retrieve()
                    .bodyToMono(SummaryShell.class)
                    .block();


            if (request == null || request.getPrecSearch() == null) {
                log.warn("응답이 비어있거나 PrecSearch 없음, page={}", page);
                break;
            }

            if (request != null && request.getPrecSearch() != null) {
                List<SummaryDetailRequest> summaries =
                        Optional.ofNullable(request.getPrecSearch().getPrec())
                                .orElse(Collections.emptyList());

                // 첫 페이지에서 전체 페이지 수 계산
                if (page == 1) {
                    try {
                        int totalCnt = Integer.parseInt(request.getPrecSearch().getTotalCnt()); // 총 건수
                        int perPage = summaries.size() > 0 ? summaries.size() : 20; // 첫 페이지 건수 기준
                        totalPages = (int) Math.ceil((double) totalCnt / perPage); // 전체 페이지 계산
                        log.info("총 건수: {}, 페이지 수: {}", totalCnt, totalPages);
                    } catch (Exception e) {
                        log.warn("totalCnt 파싱 실패", e);
                        totalPages = 1; // 실패하면 첫 페이지만 처리
                    }
                }

                List<PrecedentSummary> entities = summaries.stream()
                        .filter(dto -> dto.getPrecedentNo() != null && !dto.getPrecedentNo().isBlank())
                        .filter(dto -> {
                            try {
                                if (dto.getSentencingDate() == null || dto.getSentencingDate().isBlank()) {
                                    log.info("제외 - 날짜 없음: {}", dto.getPrecedentNo());
                                    return false;
                                }
                                String rawDate = dto.getSentencingDate().trim();
                                LocalDate decisionDate = LocalDate.parse(rawDate, formatter);
                                boolean recent = !decisionDate.isBefore(tenYearsAgo);
                                if (!recent) log.info("제외 - 10년 초과: {} ({})", dto.getPrecedentNo(), rawDate);
                                return recent;
                            } catch (Exception e) {
                                log.warn("날짜 파싱 실패: {} ({})", dto.getPrecedentNo(), dto.getSentencingDate(), e);
                                return false;
                            }
                        })
                        .map(dto -> new PrecedentSummary(
                                dto.getPrecedentNo(),
                                dto.getCaseNo(),
                                dto.getDataSource(),
                                dto.getCaseTypeCode(),
                                dto.getCaseTypeName(),
                                dto.getSentencing(),
                                dto.getSentencingDate(),
                                dto.getCourtName(),
                                dto.getJudgmentType(),
                                dto.getCaseName()
                        ))
                        .toList();

                precedentSummaryRepository.saveAll(entities);

                if (!entities.isEmpty()) {
                    precedentSummaryRepository.saveAll(entities);
                    log.info("page={} 저장 완료: {}건", page, entities.size());
                }

                page++;
            }
            log.info("판례 수집 종료, 키워드: {}", keywords);
        }
    }
}
package texhelper.chatservice.domain.precedent.service;

import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;
import texhelper.chatservice.domain.precedent.dto.request.SummaryDetailRequest;
import texhelper.chatservice.domain.precedent.entity.PrecedentSummary;
import texhelper.chatservice.domain.precedent.repository.PrecedentSummaryRepository;

import java.util.List;

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
        log.info("Fetching summaries for keyword: {}", keywords);
        List<SummaryDetailRequest> summaries = webClient.get()
                .uri(uriBuilder -> uriBuilder
                        .path("/lawSearch.do")
                        .queryParam("OC", email)
                        .queryParam("target", "prec")
                        .queryParam("type", "JSON")
                        .queryParam("query", keywords)
                        .build())
                .retrieve()
                .bodyToFlux(SummaryDetailRequest.class)
                .collectList()
                .block();
        log.info("Fetched {} summaries", summaries != null ? summaries.size() : 0);
        if (summaries != null && !summaries.isEmpty()) {
            summaries.forEach(s -> log.info("DTO precedentNo1: {}", s.getPrecedentNo()));
            List<PrecedentSummary> entities = summaries.stream()
                    .filter(dto -> dto.getPrecedentNo() != null && !dto.getPrecedentNo().isBlank())
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
            entities.forEach(e -> log.info("Saving precedentNo: {}", e.getPrecedentNo()));
            precedentSummaryRepository.saveAll(entities);
        }
    }
}

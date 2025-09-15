package texhelper.chatservice.domain.precedent.service;

import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;
import texhelper.chatservice.domain.precedent.dto.request.SummaryDetailRequest;
import texhelper.chatservice.domain.precedent.dto.request.SummaryShell;
import texhelper.chatservice.domain.precedent.entity.PrecedentSummary;
import texhelper.chatservice.domain.precedent.repository.PrecedentSummaryRepository;

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

        if (request != null && request.getPrecSearch() != null) {
            List<SummaryDetailRequest> summaries =
                    Optional.ofNullable(request.getPrecSearch().getPrec())
                            .orElse(Collections.emptyList());

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

            precedentSummaryRepository.saveAll(entities);
        }
    }
}
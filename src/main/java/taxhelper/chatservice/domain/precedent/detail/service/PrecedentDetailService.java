package taxhelper.chatservice.domain.precedent.detail.service;

import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.util.UriComponentsBuilder;
import taxhelper.chatservice.domain.precedent.detail.dto.DetailShellRequest;
import taxhelper.chatservice.domain.precedent.detail.entity.PrecedentDetail;
import taxhelper.chatservice.domain.precedent.detail.repository.PrecedentDetailRepository;
import taxhelper.chatservice.domain.precedent.repository.PrecedentSummaryRepository;

import java.util.List;

@Service
@Slf4j
public class PrecedentDetailService {

    private final WebClient webClient;
    private final PrecedentDetailRepository precedentDetailRepository;
    private final PrecedentSummaryRepository precedentSummaryRepository;

    public PrecedentDetailService(WebClient webClient, PrecedentDetailRepository precedentDetailRepository, PrecedentSummaryRepository precedentSummaryRepository) {
        this.webClient = webClient;
        this.precedentDetailRepository = precedentDetailRepository;
        this.precedentSummaryRepository = precedentSummaryRepository;
    }

    @Value("${law.api.email}")
    private String email;

    @Transactional
    public void fetchDetails() {

        List<String> precedentNos = precedentSummaryRepository.findAllPrecedentNo();

        for (String precedentNo: precedentNos) {
            String uri = UriComponentsBuilder.fromPath("/lawService.do")
                    .queryParam("OC", email)
                    .queryParam("target", "prec")
                    .queryParam("ID", precedentNo)
                    .queryParam("type", "JSON")
                    .build()
                    .toUriString();

            DetailShellRequest request = webClient.get()
                    .uri(uri)
                    .retrieve()
                    .bodyToMono(DetailShellRequest.class)
                    .block();


            if (request != null && request.getPrecService() != null) {
                PrecedentDetail entity = PrecedentDetail.builder()
                        .precedentNo(precedentNo)
                        .decision(request.getPrecService().getDecision())
                        .referencePrecedent(request.getPrecService().getReferencePrecedent())
                        .caseTypeName(request.getPrecService().getCastTypeName())
                        .summaryOfJudgment(request.getPrecService().getSummaryOfJudgment())
                        .referenceClause(request.getPrecService().getReferenceClause())
                        .sentencingDate(request.getPrecService().getSentencingDate())
                        .courtName(request.getPrecService().getCourtName())
                        .caseName(request.getPrecService().getCaseName())
                        .precedentDetails(request.getPrecService().getPrecedentDetails())
                        .caseNo(request.getPrecService().getCaseNo())
                        .caseCode(request.getPrecService().getCaseCode())
                        .sentencing(request.getPrecService().getSentencing())
                        .judgmentType(request.getPrecService().getJudgmentType())
                        .courtCode(request.getPrecService().getCourtCode())
                        .build();
                log.info("Saving precedentNo={}, caseName={}", entity.getPrecedentNo(), entity.getSummaryOfJudgment());
                precedentDetailRepository.save(entity);
            }else{
                log.warn("No detail found for precedentNo={}", precedentNo);
            }
        }
    }
}
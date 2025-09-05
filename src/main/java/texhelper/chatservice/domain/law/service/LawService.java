package texhelper.chatservice.domain.law.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;
import texhelper.chatservice.common.config.RestClientConfig;
import texhelper.chatservice.domain.law.dto.request.*;
import texhelper.chatservice.domain.law.entity.*;
import texhelper.chatservice.domain.law.repository.LawRepository;

import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.stream.Collectors;
import java.util.stream.Stream;

@Service
@Slf4j
public class LawService {

    private final RestTemplate restTemplate;
    private final ObjectMapper objectMapper;
    private final RestClientConfig restClientConfig;
    private final LawRepository lawRepository;

    public LawService(RestTemplate restTemplate, ObjectMapper objectMapper, RestClientConfig restClientConfig, LawRepository lawRepository) {
        this.restTemplate = restTemplate;
        this.objectMapper = objectMapper;
        this.restClientConfig = restClientConfig;
        this.lawRepository = lawRepository;
    }




    public void saveLawFromApi() {
        try {
            LawRequest lawRequest = restClientConfig.fetchLaw(restTemplate, objectMapper, "127280");
            // 1. Law 생성 (Amendments 리스트는 엔티티에서 이미 new ArrayList<>로 초기화)
            Law lawEntity = lawRequest.get법령().toEntity();

            //   lawRepository.saveAndFlush(lawEntity);

            if (lawRequest.get법령().get기본정보() != null) {

                MinistryRequest mDto = lawRequest.get법령().get기본정보().get소관부처();
                Ministry ministries = Ministry.of(null, mDto.getMinistryName(), mDto.getMinistryCode(), lawEntity, new ArrayList<>());
                lawEntity.getMinistry().add(ministries);

                BasicInfo info = lawRequest.get법령().get기본정보();
                if (info.getContactDepartment()  != null && info.getContactDepartment() .getDepartmentUnit() != null) {
                    DepartmentUnitRequest dDto = info.getContactDepartment().getDepartmentUnit();

                    DepartmentUnit de = DepartmentUnit.builder()
                            .deptName(dDto.getDeptName())
                            .deptPhone(dDto.getDeptPhone())
                            .deptKey(dDto.getDeptKey())
                            .build();

                    ministries.addDepartmentUnit(de);
                }
                LawType lawType = LawType.builder()
                        .lawTypeId(null)
                        .lawType(lawRequest.get법령().get기본정보().get법종구분().getLawType())
                        .lawTypeCode(lawRequest.get법령().get기본정보().get법종구분().getLawType())
                        .build();

                lawEntity.addLawType(lawType);

                // 2. Amendments 생성 후 Law에 추가
                if (lawRequest.get법령().get개정문() != null) {
                    Amendments amendments = Amendments.builder()
                            .title(lawRequest.get법령().get개정문().getTitle())
                            .content(lawRequest.get법령().get개정문().getContent().toString())
                            .law(lawEntity)
                            .build();

                    lawEntity.getAmendments().add(amendments);
                }

                if (lawRequest.get법령().get조문() != null) {
                    for (ArticleUnitRequest dto : lawRequest.get법령().get조문().getUnits()) {

                        String referenceStr = dto.getReference() != null
                                ? dto.getReference().stream()
                                .filter(Objects::nonNull)
                                .flatMap(list -> list != null ? list.stream() : Stream.empty())
                                .filter(Objects::nonNull)
                                .collect(Collectors.joining("\n"))
                                : "";  // null이면 빈 문자열                     // 줄바꿈으로 합치기

                        Articles article = Articles.builder()
                                .articleNo(dto.getArticleNo())
                                .title(dto.getTitle())
                                .content(dto.getContent())
                                .enforcementDate(dto.getEnforcementDate())
                                .isChanged(dto.getIsChanged())
                                .moveBefore(dto.getMoveBefore())
                                .moveAfter(dto.getMoveAfter())
                                .reference(referenceStr)
                                .articleType(dto.getArticleType())
                                .branchNo(dto.getBranchNo())
                                .law(lawEntity)
                                .build();


                        // Paragraph 연결
                        for (ParagraphRequest pDto : dto.getParagraph()) {
                            Paragraph paragraph = Paragraph.builder()
                                    .pargraphNo(pDto.getPargraphNo())
                                    .revisionType(pDto.getRevisionType())
                                    .revisionTypeStr(pDto.getRevisionTypeStr())
                                    .content(String.join("\n", pDto.getContent()))
                                    .build();
                            article.addParagraph(paragraph);
                            //item 연결
                            for (ItemRequest iDto : pDto.getItem()) {
                                Item item = Item.builder()
                                        .itemNo(iDto.getItemNo())
                                        .content(iDto.getContent())
                                        .build();

                                paragraph.addItem(item); // Paragraph 안에 Item 추가

                                for (SubItemRequest sDto : iDto.getSubItem()) {
                                    SubItem subItem = sDto.toEntity();
                                    item.addSubItem(subItem); // Item 안에 SubItem 추가
                                }
                            }
                        }

                        lawEntity.getArticles().add(article);
                    }
                }

                if (lawRequest.get법령().get부칙() != null) {

                    for (SupplementUnitRequest dto : lawRequest.get법령().get부칙().getUnits()) {
                        List<List<String>> contentLists = dto.getContent();
                        String content = (contentLists.stream()
                                .flatMap(List::stream)      // 내부 리스트를 풀어서 스트림으로
                                .collect(Collectors.joining("\n"))); // 줄바꿈으로 합치기

                        Supplements supplements = Supplements.of(
                                null,                          // supplementsId (auto generated)
                                dto.getProclamationNo(),
                                dto.getProclamationDate(),
                                content,
                                lawEntity
                        );
                        lawEntity.getSupplements().add(supplements);
                    }
                }

                lawRepository.saveAndFlush(lawEntity);

            }
        } catch (Exception e) {
            log.error("법령 저장 중 알 수 없는 에러 발생", e);
        }
    }
}
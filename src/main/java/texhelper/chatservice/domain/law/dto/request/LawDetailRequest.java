package texhelper.chatservice.domain.law.dto.request;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;
import texhelper.chatservice.domain.law.entity.Amendments;
import texhelper.chatservice.domain.law.entity.Law;

import java.util.ArrayList;
import java.util.List;

@Getter
@NoArgsConstructor
@AllArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class LawDetailRequest {

    @JsonProperty("기본정보")
    private BasicInfo 기본정보;

    @JsonProperty("법령키")
    private Long lawKey;

    @JsonProperty("조문")
    private ArticleRequest 조문;

    @JsonProperty("부칙")
    private SupplementsRequest 부칙;

    @JsonProperty("개정문")
    private AmendmentsRequest 개정문;

    public Law toEntity() {
        return Law.builder()
                .lawKey(lawKey != null ? lawKey : null)
                .lawId(기본정보 != null && 기본정보.getLawId() != null ? Long.parseLong(기본정보.getLawId()) : null)
                .nameKor(기본정보 != null ? 기본정보.getNameKor() : null)
                .nameHanja(기본정보 != null ? 기본정보.getNameHanja() : null)
                .nameShort(기본정보 != null ? 기본정보.getNameShort() : null)
                .decisionType(기본정보 != null ? 기본정보.getDecisionType() : null)
                .proposalType(기본정보 != null ? 기본정보.getProposalType() : null)
                .proclamationNo(기본정보 != null ? 기본정보.getProclamationNo() : null)
                .proclamationDate(기본정보 != null ? 기본정보.getProclamationDate() : null)
                .enforcementDate(기본정보 != null ? 기본정보.getEnforcementDate() : null)
                .phoneNumber(기본정보 != null ? 기본정보.getPhoneNumber() : null)
                .language(기본정보 != null ? 기본정보.getLanguage() : null)
                .revisionType(기본정보 != null ? 기본정보.getRevisionType() : null)
                .joinMinistry(기본정보 != null ? 기본정보.getJoinMinistry() : null)
                .isProclaimed(기본정보 != null ? 기본정보.getIsProclaimed() : null)
                .isKorean(기본정보 != null ? 기본정보.getIsKorean() : null)
                .isTitleChange(기본정보 != null ? 기본정보.getIsTitleChange() : null)
                .hasAnnex(기본정보 != null ? 기본정보.getHasAnnex() : null)
                .structureCode(기본정보 != null ? 기본정보.getStructureCode() : null)
                .articles(new ArrayList<>())
                .supplements(new ArrayList<>())
                .amendments(new ArrayList<>())
                .build();
    }
}

package taxhelper.chatservice.domain.law.dto.request;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;

@Getter
@NoArgsConstructor
@AllArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class BasicInfo {

    @JsonProperty("법령ID")
    private String lawId;

    @JsonProperty("법령명_한글")
    private String nameKor;

    @JsonProperty("법령명_한자")
    private String nameHanja;

    @JsonProperty("법령명약칭")
    private String nameShort;

    @JsonProperty("의결구분")
    private String decisionType;

    @JsonProperty("제안구분")
    private String proposalType;

    @JsonProperty("공포번호")
    private String proclamationNo;

    @JsonProperty("공포일자")
    private String proclamationDate;

    @JsonProperty("시행일자")
    private String enforcementDate;

    @JsonProperty("전화번호")
    private String phoneNumber;

    @JsonProperty("언어")
    private String language;

    @JsonProperty("제개정여부")
    private String revisionType;

    @JsonProperty("제개정구분")
    private String revisionClassification;

    @JsonProperty("공동부령정보")
    private String joinMinistry;

    @JsonProperty("공포법령여부")
    private String isProclaimed;

    @JsonProperty("한글법령여부")
    private String isKorean;

    @JsonProperty("제명변경여부")
    private String isTitleChange;

    @JsonProperty("별표편집여부")
    private String hasAnnex;

    @JsonProperty("편장절관")
    private String structureCode;

    @JsonProperty("소관부처")
    private MinistryRequest 소관부처;

    @JsonProperty("법종구분")
    private LawTypeRequest 법종구분;

    @JsonProperty("연락부서")
    private ContactDepartment contactDepartment;
}

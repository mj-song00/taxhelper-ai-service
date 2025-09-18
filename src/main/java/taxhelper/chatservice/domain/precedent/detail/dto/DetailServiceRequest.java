package taxhelper.chatservice.domain.precedent.detail.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.*;

@Getter
@NoArgsConstructor
@AllArgsConstructor
@Builder
@Setter
public class DetailServiceRequest {

    @JsonProperty("판시사항")
    private String decision; //판시사항

    @JsonProperty("참조판례")
    private String referencePrecedent; //참조판례

    @JsonProperty("사건종류명")
    private String castTypeName; //사건종류명

    @JsonProperty("판결요지")
    private String summaryOfJudgment; //판결요지

    @JsonProperty("참조조문")
    private String referenceClause; //참조조문

    @JsonProperty("선고일자")
    private String sentencingDate; //선고일자

    @JsonProperty("법원명")
    private String courtName; //법원명

    @JsonProperty("사건명")
    private String caseName; //사건명

    @JsonProperty("판례내용")
    private String precedentDetails; //판례내용

    @JsonProperty("사건번호")
    private String caseNo; //사건번호

    @JsonProperty("사건종류코드")
    private String caseCode; // 사건 종류 코드

    @JsonProperty("판례정보일련번호")
    private String precedentNo; //판례정보일련번호

    @JsonProperty("선고")
    private String sentencing; //선고

    @JsonProperty("판결유형")
    private String judgmentType; // 판결유형

    @JsonProperty("법원종류코드")
    private String courtCode; // 법원코드

}

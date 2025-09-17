package taxhelper.chatservice.domain.precedent.dto.request;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;

@Getter
@NoArgsConstructor
@AllArgsConstructor
public class DetailRequest {

    @JsonProperty("판시사항")
    private String judgment; //판시사항

    @JsonProperty("참조판례")
    private String referencePrecedent; //참조 판례

    @JsonProperty("사건종류")
    private String typeOfIncident; // 사건 종류

    @JsonProperty("판결요지")
    private String summaryOfJudgment; // 판결 요지

    @JsonProperty("선고일자")
    private String sentencingDate; // 선고일자

    @JsonProperty("법원명")
    private String courtName; // 법원명

    @JsonProperty("사건명")
    private String  caseName; //사건명

    @JsonProperty("판례내용")
    private String detailsOfPrecedent; // 판례내용

    @JsonProperty("사건번호")
    private String caseNo; //사건번호

    @JsonProperty("사건종류코드")
    private String caseCode; // 사건 종류 코드

    @JsonProperty("판례번호")
    private String precedentNo; //판례번호

    @JsonProperty("선고")
    private String sentencing; //선고

    @JsonProperty("판결유형")
    private String judgmentType; // 판결유형

    @JsonProperty("법원코드")
    private String courtCode; // 법원코드

}

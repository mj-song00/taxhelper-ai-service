package taxhelper.chatservice.domain.precedent.dto.request;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Getter;
import lombok.Setter;

@Getter
@Setter
public class SummaryDetailRequest {

    @JsonProperty("사건번호")
    private String caseNo;

    @JsonProperty("데이터출처명")
    private String dataSource;

    @JsonProperty("사건종류코드")
    private String caseTypeCode; //사건 종류 코드

    @JsonProperty("사건종류명")
    private String  caseTypeName; //사건종류명

    @JsonProperty("선고")
    private String sentencing; //선고

    @JsonProperty("선고일자")
    private String sentencingDate; //선고일자

    @JsonProperty("법원명")
    private String courtName; // 법원명

    @JsonProperty("판례일련번호")
    private String precedentNo; // 판례 일련번호

    @JsonProperty("판결유형")
    private String judgmentType; // 판결유형

    @JsonProperty("사건명")
    private String caseName; // 사건명
}

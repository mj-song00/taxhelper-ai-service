package taxhelper.chatservice.domain.law.dto.request;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;

@Getter
@NoArgsConstructor
@AllArgsConstructor
public class DepartmentUnitRequest {

    @JsonProperty("부서명")
    private String deptName;

    @JsonProperty("부서연락처")
    private String deptPhone;

    @JsonProperty("부서키")
    private String deptKey;

    @JsonProperty("소관부처명")
    private String ministryName;

    @JsonProperty("소관부처코드")
    private String ministryCode;

}

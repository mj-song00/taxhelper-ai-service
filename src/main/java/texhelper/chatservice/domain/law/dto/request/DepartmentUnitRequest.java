package texhelper.chatservice.domain.law.dto.request;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;
import texhelper.chatservice.domain.law.entity.DepartmentUnit;

@Getter
@NoArgsConstructor
@AllArgsConstructor
public class DepartmentUnitRequest {


    @JsonProperty("부서명")
    private String deptName;

    @JsonProperty("부서연락처")
    private String deptPhone;

    public DepartmentUnit toEntity(){
        return new DepartmentUnit();
    }
}

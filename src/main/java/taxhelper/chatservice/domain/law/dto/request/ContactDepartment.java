package taxhelper.chatservice.domain.law.dto.request;


import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;

@Getter
@NoArgsConstructor
@AllArgsConstructor
public class ContactDepartment {
    @JsonProperty("부서단위")
    private DepartmentUnitRequest departmentUnit;
}

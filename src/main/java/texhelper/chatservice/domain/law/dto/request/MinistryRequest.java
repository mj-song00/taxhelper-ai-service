package texhelper.chatservice.domain.law.dto.request;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.persistence.CascadeType;
import jakarta.persistence.OneToMany;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;
import texhelper.chatservice.domain.law.entity.DepartmentUnit;
import texhelper.chatservice.domain.law.entity.Ministry;

import java.util.ArrayList;
import java.util.List;

@Getter
@NoArgsConstructor
@AllArgsConstructor
public class MinistryRequest {

    @JsonProperty("content")
    private String ministryName;

    @JsonProperty("소관부처코드")
    private String ministryCode;

}

package taxhelper.chatservice.domain.law.dto.request;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;

@Getter
@NoArgsConstructor
@AllArgsConstructor
public class MinistryRequest {

    @JsonProperty("content")
    private String ministryName;

    @JsonProperty("소관부처코드")
    private String ministryCode;

}

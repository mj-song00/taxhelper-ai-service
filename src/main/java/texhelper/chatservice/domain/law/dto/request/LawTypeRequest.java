package texhelper.chatservice.domain.law.dto.request;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;
import texhelper.chatservice.domain.law.entity.LawType;

@Getter
@NoArgsConstructor
@AllArgsConstructor
public class LawTypeRequest {

    @JsonProperty("content")
    private String lawType;

    @JsonProperty("법종구분코드")
    private String lawTypeCode;

    public LawType toEntity(){
        return new LawType();
    }
}

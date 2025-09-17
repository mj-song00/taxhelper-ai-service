package taxhelper.chatservice.domain.precedent.detail.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Getter;

@Getter
public class DetailShellRequest {
    @JsonProperty("PrecService")
    private  DetailServiceRequest precService;
}

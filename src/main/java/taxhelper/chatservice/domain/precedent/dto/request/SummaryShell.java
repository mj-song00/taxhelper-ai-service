package taxhelper.chatservice.domain.precedent.dto.request;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Getter;
import lombok.Setter;

@Getter
@Setter
public class SummaryShell {

    @JsonProperty("PrecSearch")
    private  SummaryRequest  PrecSearch;
}

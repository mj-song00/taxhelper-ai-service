package texhelper.chatservice.domain.precedent.dto.request;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Getter;

@Getter
public class SummaryShell {

    @JsonProperty("PrecSearch")
    private  SummaryRequest  PrecSearch;
}

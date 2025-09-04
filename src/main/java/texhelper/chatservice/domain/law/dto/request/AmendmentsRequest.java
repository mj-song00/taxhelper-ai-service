package texhelper.chatservice.domain.law.dto.request;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;
import texhelper.chatservice.domain.law.entity.Amendments;

import java.util.List;

@Getter
@NoArgsConstructor
@AllArgsConstructor
public class AmendmentsRequest {

    @JsonProperty("개정문")
    private String title;

    @JsonProperty("개정문내용")
    private List<List<String>> content;

    public Amendments toEntity(){
        return Amendments.builder()
                .title("개정문")
                .content(content != null ? content.toString() : null)
                .build();
    }
}

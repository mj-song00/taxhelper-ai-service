package texhelper.chatservice.domain.law.dto.request;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;
import texhelper.chatservice.domain.law.entity.Supplements;

import java.util.List;

@Getter
@NoArgsConstructor
@AllArgsConstructor
public class SupplementUnitRequest {

    @JsonProperty("부칙키")
    private String key;

    @JsonProperty("부칙공포번호")
    private Long proclamationNo;

    @JsonProperty("부칙공포일자")
    private String proclamationDate;

    @JsonProperty("부칙내용")
    private  List<List<String>> content;



    public Supplements toEntity(){
        return Supplements.builder()
                .proclamationNo(proclamationNo)
                .proclamationDate(proclamationDate)
                .content(content != null ? content.toString() : null)
                .build();
    }
}

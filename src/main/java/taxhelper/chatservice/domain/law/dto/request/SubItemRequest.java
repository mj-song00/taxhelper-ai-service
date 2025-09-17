package taxhelper.chatservice.domain.law.dto.request;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.annotation.JsonDeserialize;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;
import taxhelper.chatservice.domain.law.StringOrArrayDeserializer;
import taxhelper.chatservice.domain.law.entity.SubItem;

import java.util.List;


@Getter
@NoArgsConstructor
@AllArgsConstructor
public class SubItemRequest {
    @JsonProperty("목번호")
    private String subNo;

    @JsonProperty("목내용")
    @JsonDeserialize(using = StringOrArrayDeserializer.class)
    private List<String>  subContent;

    public SubItem toEntity() {
        return SubItem.builder()
                .subItemNo(subNo)
                .subContent(subContent != null
                        ? String.join("\n", subContent)   // 엔티티가 String이면 join
                        : null)
                .build();
    }
}

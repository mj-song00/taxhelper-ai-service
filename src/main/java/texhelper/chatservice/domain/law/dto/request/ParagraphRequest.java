package texhelper.chatservice.domain.law.dto.request;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;
import texhelper.chatservice.domain.law.entity.Item;
import texhelper.chatservice.domain.law.entity.Paragraph;

import java.util.ArrayList;
import java.util.List;

@Getter
@NoArgsConstructor
@AllArgsConstructor
public class ParagraphRequest {

    @JsonProperty("항번호")
    private String pargraphNo;

    @JsonProperty("항제개정유형")
    private String revisionType;

    @JsonProperty("항제개정일자문자열")
    private String revisionTypeStr;

    @JsonProperty("항내용")
    private String content;

    @JsonProperty("호")
    private List<ItemRequest> item = new ArrayList<>();


    public Paragraph toEntity(){

        List<Item> allItems = item.stream()
                .map(ItemRequest::toEntity)
                .toList();

        return Paragraph.builder()
                .pargraphNo(pargraphNo)
                .revisionType(revisionType)
                .revisionTypeStr(revisionTypeStr)
                .content(content)
                .item(allItems)
                .build();
    }
}

package taxhelper.chatservice.domain.law.dto.request;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;
import taxhelper.chatservice.domain.law.entity.Item;

import java.util.ArrayList;
import java.util.List;

@Getter
@NoArgsConstructor
@AllArgsConstructor
public class ItemRequest {
    @JsonProperty("호번호")
    private String itemNo;

    @JsonProperty("호내용")
    private String content;

    @JsonProperty("호가지번호")
    private String branchNO;

    @JsonProperty("목")
    private List<SubItemRequest> subItem = new ArrayList<>();

    public Item toEntity(){
        return Item.builder()
                .itemNo(itemNo)
                .content(content)
                .subItem(subItem != null
                        ? subItem.stream()
                        .map(SubItemRequest::toEntity)
                        .toList()
                        : null)
                .build();
    }
}

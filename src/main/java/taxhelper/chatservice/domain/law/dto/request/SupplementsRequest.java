package taxhelper.chatservice.domain.law.dto.request;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;
import taxhelper.chatservice.domain.law.entity.Supplements;

import java.util.List;
import java.util.stream.Collectors;

@Getter
@NoArgsConstructor
@AllArgsConstructor
public class SupplementsRequest {

    @JsonProperty("부칙단위")
    private List<SupplementUnitRequest> units;

    public List<Supplements> toEntityList() {
        if (units == null) return null;
        return units.stream()
                .map(SupplementUnitRequest::toEntity)
                .collect(Collectors.toList());
    }
}

package taxhelper.chatservice.domain.precedent.dto.request;

import com.fasterxml.jackson.annotation.JsonFormat;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import lombok.Getter;
import lombok.Setter;

import java.util.List;
/**
 * todo setter는 testcode용으로만 사용합니다.
 */
@Getter
@Setter
@JsonIgnoreProperties(ignoreUnknown = true)
public class SummaryRequest {

    @JsonFormat(with = JsonFormat.Feature.ACCEPT_SINGLE_VALUE_AS_ARRAY)
    private List<SummaryDetailRequest> prec;

    private String totalCnt;
}
